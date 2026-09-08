from datetime import UTC, datetime

import httpx
import respx
from cryptography.fernet import Fernet

from app.calendar.google_calendar import GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL
from app.models import User, Workspace
from app.repositories.oauth_state_repository import OAuthStateRepository


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

        state = await OAuthStateRepository(db_session).issue(
            "google_link", team_id="T1", slack_user_id="U_ALICE"
        )
        await db_session.commit()

        link_response = await api_client.get(
            "/google/link", params={"state": state}, follow_redirects=False
        )
        assert link_response.status_code in (302, 307)
        redirected_state = link_response.headers["location"].split("state=")[1].split("&")[0]
        assert redirected_state == state

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

    async def test_link_rejects_a_forged_or_unknown_state(self, api_client, monkeypatch):
        """The actual S1 vulnerability: /google/link used to accept
        team_id/slack_user_id as plain query params, so anyone could complete
        Google OAuth and bind their own refresh token to a victim's Slack
        identity just by guessing/observing their Slack user ID. Now the
        endpoint has no identity parameter to forge at all — only a state
        token it did not issue, which must be rejected outright."""
        _configure(monkeypatch, Fernet.generate_key().decode())
        response = await api_client.get(
            "/google/link", params={"state": "attacker-supplied-garbage"}, follow_redirects=False
        )
        assert response.status_code == 400

    async def test_link_rejects_an_expired_state(self, api_client, db_session, monkeypatch):
        _configure(monkeypatch, Fernet.generate_key().decode())
        state = await OAuthStateRepository(db_session).issue(
            "google_link", team_id="T1", slack_user_id="U_ALICE", ttl_seconds=-1
        )
        await db_session.commit()

        response = await api_client.get(
            "/google/link", params={"state": state}, follow_redirects=False
        )
        assert response.status_code == 400

    async def test_link_rejects_a_state_issued_for_a_different_purpose(
        self, api_client, db_session, monkeypatch
    ):
        _configure(monkeypatch, Fernet.generate_key().decode())
        state = await OAuthStateRepository(db_session).issue(
            "slack_install", team_id="T1", slack_user_id="U_ALICE"
        )
        await db_session.commit()

        response = await api_client.get(
            "/google/link", params={"state": state}, follow_redirects=False
        )
        assert response.status_code == 400
