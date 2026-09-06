"""The natural-language layer, added last per ARCHITECTURE.md's
tools-first-LLM-last principle. The model only ever picks a tool from
app/agent/tools.py's whitelist — it never touches the database directly and
never supplies team_id/slack_user_id itself; those live on AgentContext,
built server-side from the authenticated Slack event.
"""

from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.agent.tools import TOOLS, AgentContext

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"


def _system_prompt() -> str:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return (
        f"You are a workplace assistant. Today's date is {today} (UTC). You can "
        "create and list tasks, and propose meeting times, for the current user by "
        "calling the provided tools. All dates you pass to tools must be ISO 8601 "
        "with an explicit UTC offset — never omit the timezone. Never invent "
        "information; if a request doesn't map to an available tool, say so plainly."
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


async def _execute_tool_call(ctx: AgentContext, call: dict) -> str:
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

    return await tool.handler(ctx, args)


async def run_agent_turn(
    ctx: AgentContext,
    http_client: httpx.AsyncClient,
    api_key: str,
    *,
    user_message: str,
) -> str:
    tool_schemas = [tool.to_openai_schema() for tool in TOOLS.values()]
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": user_message},
    ]

    message = await _call_groq(http_client, api_key, messages, tools=tool_schemas)
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return message.get("content", "")

    messages.append(message)
    for call in tool_calls:
        result_text = await _execute_tool_call(ctx, call)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_text})

    final_message = await _call_groq(http_client, api_key, messages)
    return final_message.get("content", "")
