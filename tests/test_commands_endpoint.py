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

    async def test_unknown_command_does_not_crash(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(command="/nonexistent", text="", team_id="T1", user_id="U1")
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
        assert "Unknown command" in response.json()["text"]


class TestLinkCalendarCommand:
    async def test_returns_a_link_url(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr(
            "app.api.routes_commands.settings.public_base_url", "https://example.ngrok-free.app"
        )
        body = _form_body(command="/link-calendar", text="", team_id="T1", user_id="U1")
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        text = response.json()["text"]
        assert "https://example.ngrok-free.app/google/link" in text
        assert "team_id=T1" in text
        assert "slack_user_id=U1" in text


class TestMeetCommand:
    async def test_bad_syntax_returns_usage(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(command="/meet", text="nonsense", team_id="T1", user_id="U1")
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert "Usage" in response.json()["text"]

    async def test_degrades_gracefully_when_calendar_not_configured(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", None)
        body = _form_body(
            command="/meet",
            text="<@U2> 30 | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00",
            team_id="T1",
            user_id="U1",
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert "isn't configured" in response.json()["text"]

    async def test_finds_and_reports_a_slot(self, api_client, db_session, monkeypatch):
        import httpx
        import respx
        from cryptography.fernet import Fernet

        from app.calendar.google_calendar import GOOGLE_FREEBUSY_URL, GOOGLE_TOKEN_URL
        from app.core.security import TokenCipher
        from app.models import User

        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_secret", "gsecret")
        key = Fernet.generate_key().decode()
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key", key)
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key_version", 1)

        await _make_workspace(db_session, "T1")
        cipher = TokenCipher(keys={1: key}, current_version=1)
        token_enc, version = cipher.encrypt("tok-alice")
        db_session.add(
            User(
                slack_user_id="U1",
                team_id="T1",
                tz="UTC",
                google_refresh_token_enc=token_enc,
                key_version=version,
                google_email="a@x.com",
            )
        )
        await db_session.commit()

        with respx.mock:
            respx.post(GOOGLE_TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"access_token": "ya29.fake"})
            )
            respx.post(GOOGLE_FREEBUSY_URL).mock(
                return_value=httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})
            )

            body = _form_body(
                command="/meet",
                text="<@U1> 30 | 2026-09-07T09:00:00+00:00 | 2026-09-07T17:00:00+00:00",
                team_id="T1",
                user_id="U1",
            )
            response = await api_client.post(
                "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
            )

        text = response.json()["text"]
        assert "Earliest mutual slot" in text
        assert "2026-09-07T09:00:00+00:00" in text
