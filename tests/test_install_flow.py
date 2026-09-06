import httpx
import respx
from cryptography.fernet import Fernet

from app.models import Workspace
from app.services.slack_oauth import SLACK_OAUTH_ACCESS_URL


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
        assert callback_response.json() == {"status": "installed", "team_id": "T555"}

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
