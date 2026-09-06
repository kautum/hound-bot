"""Slack's per-workspace OAuth flow — the bot-token half of ARCHITECTURE.md's
two OAuth flows. Hand-rolled rather than via a framework's OAuth abstraction:
the exchange itself is simple enough that owning it directly is less code and
more auditable than adapting to a library's installation-store interface.

Least-privilege scopes only (see ARCHITECTURE.md's security boundary) —
deliberately not broad channel history.
"""

from urllib.parse import urlencode

import httpx

SLACK_OAUTH_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_OAUTH_ACCESS_URL = "https://slack.com/api/oauth.v2.access"

BOT_SCOPES = [
    "app_mentions:read",
    "chat:write",
    "commands",
    "users:read",
    "im:history",
    "im:write",
]


class SlackOAuthError(Exception):
    """Slack's own API returned ok: false, or the response shape was unexpected."""


def build_install_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "scope": ",".join(BOT_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{SLACK_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(
    http_client: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> tuple[str, str]:
    """Returns (bot_token, team_id). Raises SlackOAuthError on any failure —
    never returns a plausible-looking result for a failed exchange."""
    response = await http_client.post(
        SLACK_OAUTH_ACCESS_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    response.raise_for_status()
    body = response.json()

    if not body.get("ok"):
        raise SlackOAuthError(f"Slack OAuth exchange failed: {body.get('error', 'unknown')}")

    try:
        bot_token = body["access_token"]
        team_id = body["team"]["id"]
    except KeyError as exc:
        raise SlackOAuthError(f"unexpected Slack OAuth response shape: missing {exc}") from exc

    return bot_token, team_id
