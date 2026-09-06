from datetime import UTC, datetime

import httpx
import respx
from cryptography.fernet import Fernet

from app.calendar.google_calendar import GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL
from app.models import User, Workspace


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


def _configure(monkeypatch, encryption_key: str) -> None:
    monkeypatch.setattr("app.api.routes_google.settings.google_client_id", "client-123")
    monkeypatch.setattr("app.api.routes_google.settings.google_client_secret", "shh")
    monkeypatch.setattr(
        "app.api.routes_google.settings.public_base_url", "https://example.ngrok-free.app"
    )
    monkeypatch.setattr("app.api.routes_google.settings.encryption_key", encryption_key)
    monkeypatch.setattr("app.api.routes_google.settings.encryption_key_version", 1)


class TestGoogleLinkFlow:
    @respx.mock
    async def test_callback_stores_encrypted_refresh_token_and_email(
        self, api_client, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        _configure(monkeypatch, key)
        await _make_workspace(db_session, "T1")

        link_response = await api_client.get(
            "/google/link",
            params={"team_id": "T1", "slack_user_id": "U_ALICE"},
            follow_redirects=False,
        )
        state = link_response.headers["location"].split("state=")[1].split("&")[0]

        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"refresh_token": "refresh-abc", "access_token": "access-xyz"}
            )
        )
        respx.get(GOOGLE_USERINFO_URL).mock(
            return_value=httpx.Response(200, json={"email": "alice@example.com"})
        )

        callback_response = await api_client.get(
            "/google/oauth/callback", params={"code": "the-code", "state": state}
        )

        assert callback_response.status_code == 200
        assert callback_response.json() == {"status": "linked", "email": "alice@example.com"}

        from sqlalchemy import select

        result = await db_session.execute(select(User).filter_by(slack_user_id="U_ALICE"))
        user = result.scalar_one()
        assert user.google_email == "alice@example.com"

        from app.core.security import TokenCipher

        cipher = TokenCipher(keys={1: key}, current_version=1)
        assert (
            cipher.decrypt(user.google_refresh_token_enc, user.key_version) == "refresh-abc"
        )

    async def test_callback_rejects_unknown_state(self, api_client, monkeypatch):
        _configure(monkeypatch, Fernet.generate_key().decode())
        response = await api_client.get(
            "/google/oauth/callback", params={"code": "x", "state": "never-issued"}
        )
        assert response.status_code == 400
