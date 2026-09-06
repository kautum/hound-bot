"""The natural-language layer, added last per ARCHITECTURE.md's
tools-first-LLM-last principle. The model only ever picks a tool from
app/agent/tools.py's whitelist — it never touches the database directly and
never supplies team_id/slack_user_id itself.
"""

import httpx
from pydantic import ValidationError

from app.agent.tools import TOOLS

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are a workplace assistant. You can create and list tasks for the "
    "current user by calling the provided tools. Never invent information; "
    "if a request doesn't map to an available tool, say so plainly."
)


class AgentError(Exception):
    """Groq's response didn't match the expected shape — never silently
    treated as an empty or plausible reply."""


async def _call_groq(
    http_client: httpx.AsyncClient, api_key: str, messages: list, tools=None
) -> dict:
    payload = {"model": MODEL, "messages": messages}
    if tools:
        payload["tools"] = tools

    response = await http_client.post(
        GROQ_CHAT_URL, headers={"Authorization": f"Bearer {api_key}"}, json=payload
    )
    response.raise_for_status()
    body = response.json()
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise AgentError(f"unexpected Groq response shape: {exc}") from exc


async def _execute_tool_call(session, team_id: str, slack_user_id: str, call: dict) -> str:
    name = call["function"]["name"]
    tool = TOOLS.get(name)
    if tool is None:
        # The model asked for something outside the whitelist. This is
        # exactly what the whitelist exists to stop — never execute it,
        # whatever the model's stated justification was.
        return f"Tool '{name}' does not exist."

    try:
        args = tool.args_model.model_validate_json(call["function"]["arguments"])
    except (ValidationError, ValueError) as exc:
        return f"Invalid arguments for {name}: {exc}"

    return await tool.handler(session, team_id, slack_user_id, args)


async def run_agent_turn(
    session,
    http_client: httpx.AsyncClient,
    api_key: str,
    *,
    team_id: str,
    slack_user_id: str,
    user_message: str,
) -> str:
    tool_schemas = [tool.to_openai_schema() for tool in TOOLS.values()]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    message = await _call_groq(http_client, api_key, messages, tools=tool_schemas)
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return message.get("content", "")

    messages.append(message)
    for call in tool_calls:
        result_text = await _execute_tool_call(session, team_id, slack_user_id, call)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_text})

    final_message = await _call_groq(http_client, api_key, messages)
    return final_message.get("content", "")
