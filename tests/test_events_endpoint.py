import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

from app.models import Workspace

SIGNING_SECRET = "test-signing-secret"


def _signed_headers(body: bytes, secret: str, timestamp: str | None = None) -> dict:
    """Independently computed oracle — mirrors Slack's documented scheme
    directly, the same way tests/test_security.py verifies the primitive."""
    ts = timestamp or str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/json",
    }


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


class TestSlackEventsEndpoint:
    async def test_url_verification_challenge_is_echoed(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_events.settings.slack_signing_secret", SIGNING_SECRET)
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()

        response = await api_client.post(
            "/slack/events", content=body, headers=_signed_headers(body, SIGNING_SECRET)
        )

        assert response.status_code == 200
        assert response.json() == {"challenge": "abc123"}

    async def test_rejects_invalid_signature(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_events.settings.slack_signing_secret", SIGNING_SECRET)
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()

        response = await api_client.post(
            "/slack/events", content=body, headers=_signed_headers(body, "wrong-secret")
        )

        assert response.status_code == 401

    async def test_app_mention_is_enqueued_exactly_once(
        self, api_client, db_session, monkeypatch
    ):
        """Replaying the same event_id 3 times (Slack's real retry behaviour
        on a missed ack) must create exactly one job — see Part 12's
        idempotency check and ARCHITECTURE.md's dedupe section."""
        monkeypatch.setattr("app.api.routes_events.settings.slack_signing_secret", SIGNING_SECRET)
        await _make_workspace(db_session, "T12345")

        body = json.dumps(
            {
                "type": "event_callback",
                "event_id": "Ev_REPLAYED_EVENT",
                "team_id": "T12345",
                "event": {"type": "app_mention", "channel": "C1", "text": "hi"},
            }
        ).encode()

        for _ in range(3):
            response = await api_client.post(
                "/slack/events", content=body, headers=_signed_headers(body, SIGNING_SECRET)
            )
            assert response.status_code == 200

        from sqlalchemy import select

        from app.models import InboundJob

        result = await db_session.execute(
            select(InboundJob).filter_by(team_id="T12345", event_type="app_mention")
        )
        jobs = result.scalars().all()
        assert len(jobs) == 1

    async def test_unhandled_event_type_is_acked_but_not_enqueued(
        self, api_client, monkeypatch
    ):
        monkeypatch.setattr("app.api.routes_events.settings.slack_signing_secret", SIGNING_SECRET)
        body = json.dumps(
            {
                "type": "event_callback",
                "event_id": "Ev_UNHANDLED",
                "team_id": "T99999",
                "event": {"type": "some_event_we_dont_handle"},
            }
        ).encode()

        response = await api_client.post(
            "/slack/events", content=body, headers=_signed_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
