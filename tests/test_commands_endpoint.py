import hashlib
import hmac
import time
from datetime import UTC, datetime

from app.models import Workspace

SIGNING_SECRET = "test-signing-secret"


async def _make_workspace(session, team_id: str) -> None:
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=b"irrelevant",
            key_version=1,
            installed_at=datetime.now(UTC),
        )
    )
    await session.flush()
    await session.commit()


def _signed_form_headers(body: bytes, secret: str) -> dict:
    ts = str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _form_body(**fields) -> bytes:
    from urllib.parse import urlencode

    return urlencode(fields).encode()


class TestSlackCommandsEndpoint:
    async def test_add_list_done_round_trip(self, api_client, db_session, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        await _make_workspace(db_session, "T1")

        add_body = _form_body(
            command="/task",
            text="add <@UASSIGNEE1|bob> Write the report | 2026-09-10T17:00+00:00",
            team_id="T1",
            user_id="U_CREATOR",
            channel_id="C1",
        )
        add_response = await api_client.post(
            "/slack/commands",
            content=add_body,
            headers=_signed_form_headers(add_body, SIGNING_SECRET),
        )
        assert add_response.status_code == 200
        assert "Created task" in add_response.json()["text"]

        list_body = _form_body(
            command="/task", text="list", team_id="T1", user_id="UASSIGNEE1", channel_id="C1"
        )
        list_response = await api_client.post(
            "/slack/commands",
            content=list_body,
            headers=_signed_form_headers(list_body, SIGNING_SECRET),
        )
        assert "Write the report" in list_response.json()["text"]

        task_id = list_response.json()["text"].split("(")[1].split(")")[0]

        done_body = _form_body(
            command="/task",
            text=f"done {task_id}",
            team_id="T1",
            user_id="UASSIGNEE1",
            channel_id="C1",
        )
        done_response = await api_client.post(
            "/slack/commands",
            content=done_body,
            headers=_signed_form_headers(done_body, SIGNING_SECRET),
        )
        assert "Marked" in done_response.json()["text"]

    async def test_bad_syntax_returns_usage_not_a_crash(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(
            command="/task", text="add nonsense", team_id="T1", user_id="U1", channel_id="C1"
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
        assert "Usage" in response.json()["text"]
