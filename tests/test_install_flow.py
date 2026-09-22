import httpx
import respx
from cryptography.fernet import Fernet

from app.models import Workspace
from app.services.slack_oauth import SLACK_OAUTH_ACCESS_URL

SLACK_API_BASE = "https://slack.com/api"


def _configure(monkeypatch, encryption_key: str | None = None):
    monkeypatch.setattr("app.api.routes_install.settings.slack_client_id", "123.456")
    monkeypatch.setattr("app.api.routes_install.settings.slack_client_secret", "shh")
    monkeypatch.setattr(
        "app.api.routes_install.settings.public_base_url", "https://example.ngrok-free.app"
    )
    if encryption_key:
        monkeypatch.setattr("app.api.routes_install.settings.encryption_key", encryption_key)
        monkeypatch.setattr("app.api.routes_install.settings.encryption_key_version", 1)


class TestInstallFlow:
    async def test_install_redirects_with_a_state_param(self, api_client, monkeypatch):
        _configure(monkeypatch)
        response = await api_client.get("/slack/install", follow_redirects=False)

        assert response.status_code in (302, 307)
        location = response.headers["location"]
        assert location.startswith("https://slack.com/oauth/v2/authorize?")
        assert "state=" in location

    @respx.mock
    async def test_callback_stores_encrypted_token_and_creates_workspace(
        self, api_client, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        _configure(monkeypatch, encryption_key=key)

        install_response = await api_client.get("/slack/install", follow_redirects=False)
        state = install_response.headers["location"].split("state=")[1].split("&")[0]

        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-real-looking-token",
                    "team": {"id": "T555", "name": "New Workspace"},
                },
            )
        )

        callback_response = await api_client.get(
            "/slack/oauth/callback", params={"code": "the-code", "state": state}
        )

        assert callback_response.status_code == 200
        assert callback_response.headers["content-type"] == "text/html; charset=utf-8"
        assert "Hound is installed" in callback_response.text

        from sqlalchemy import select

        result = await db_session.execute(select(Workspace).filter_by(team_id="T555"))
        workspace = result.scalar_one()
        assert workspace.bot_token_enc != b"xoxb-real-looking-token"  # actually encrypted

        from app.core.security import TokenCipher

        cipher = TokenCipher(keys={1: key}, current_version=1)
        assert (
            cipher.decrypt(workspace.bot_token_enc, workspace.key_version)
            == "xoxb-real-looking-token"
        )

    async def test_callback_rejects_an_unknown_state(self, api_client, monkeypatch):
        _configure(monkeypatch, encryption_key=Fernet.generate_key().decode())
        response = await api_client.get(
            "/slack/oauth/callback", params={"code": "the-code", "state": "never-issued"}
        )
        assert response.status_code == 400

    @respx.mock
    async def test_callback_sends_welcome_dm_when_authed_user_present(
        self, api_client, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        _configure(monkeypatch, encryption_key=key)

        install_response = await api_client.get("/slack/install", follow_redirects=False)
        state = install_response.headers["location"].split("state=")[1].split("&")[0]

        # Mock OAuth exchange with authed_user
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-real-looking-token",
                    "team": {"id": "T555", "name": "New Workspace"},
                    "authed_user": {"id": "U12345"},
                },
            )
        )

        # Mock conversations.open
        conversations_route = respx.post(f"{SLACK_API_BASE}/conversations.open").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "channel": {"id": "D12345"}},
            )
        )

        # Mock chat.postMessage
        chat_route = respx.post(f"{SLACK_API_BASE}/chat.postMessage").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True, "ts": "1234567890.123456"},
            )
        )

        callback_response = await api_client.get(
            "/slack/oauth/callback", params={"code": "the-code", "state": state}
        )

        assert callback_response.status_code == 200
        assert "Hound is installed" in callback_response.text

        # Verify the Slack API calls were made
        assert conversations_route.call_count == 1
        assert chat_route.call_count == 1

        # Verify workspace was still created
        from sqlalchemy import select

        result = await db_session.execute(select(Workspace).filter_by(team_id="T555"))
        workspace = result.scalar_one()
        assert workspace is not None

    @respx.mock
    async def test_callback_succeeds_when_authed_user_absent(
        self, api_client, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        _configure(monkeypatch, encryption_key=key)

        install_response = await api_client.get("/slack/install", follow_redirects=False)
        state = install_response.headers["location"].split("state=")[1].split("&")[0]

        # Mock OAuth exchange without authed_user
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-real-looking-token",
                    "team": {"id": "T555", "name": "New Workspace"},
                },
            )
        )

        callback_response = await api_client.get(
            "/slack/oauth/callback", params={"code": "the-code", "state": state}
        )

        assert callback_response.status_code == 200
        assert "Hound is installed" in callback_response.text

        # Verify workspace was still created
        from sqlalchemy import select

        result = await db_session.execute(select(Workspace).filter_by(team_id="T555"))
        workspace = result.scalar_one()
        assert workspace is not None

    @respx.mock
    async def test_callback_succeeds_when_dm_send_fails(
        self, api_client, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        _configure(monkeypatch, encryption_key=key)

        install_response = await api_client.get("/slack/install", follow_redirects=False)
        state = install_response.headers["location"].split("state=")[1].split("&")[0]

        # Mock OAuth exchange with authed_user
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-real-looking-token",
                    "team": {"id": "T555", "name": "New Workspace"},
                    "authed_user": {"id": "U12345"},
                },
            )
        )

        # Mock conversations.open to fail
        respx.post(f"{SLACK_API_BASE}/conversations.open").mock(
            return_value=httpx.Response(
                200,
                json={"ok": False, "error": "user_not_found"},
            )
        )

        callback_response = await api_client.get(
            "/slack/oauth/callback", params={"code": "the-code", "state": state}
        )

        # Install should still succeed despite DM failure
        assert callback_response.status_code == 200
        assert "Hound is installed" in callback_response.text

        # Verify workspace was still created
        from sqlalchemy import select

        result = await db_session.execute(select(Workspace).filter_by(team_id="T555"))
        workspace = result.scalar_one()
        assert workspace is not None
