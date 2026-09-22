from datetime import UTC, datetime

from cryptography.fernet import Fernet

from app.core.security import TokenCipher


async def _make_workspace(session, team_id: str, cipher: TokenCipher | None = None) -> None:
    from app.models import Workspace

    if cipher:
        token_enc, version = cipher.encrypt("xoxb-fake")
    else:
        token_enc, version = b"irrelevant", 1
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=token_enc,
            key_version=version,
            installed_at=datetime.now(UTC),
        )
    )
    await session.flush()
    await session.commit()


class TestInternalTickEndpoint:
    async def test_rejects_missing_or_wrong_secret(self, api_client, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes_internal.settings.cron_shared_secret", "the-real-secret"
        )

        no_header = await api_client.post("/internal/tick")
        assert no_header.status_code == 401

        wrong_header = await api_client.post(
            "/internal/tick", headers={"X-Cron-Secret": "wrong"}
        )
        assert wrong_header.status_code == 401

    async def test_accepts_correct_secret_and_runs(self, api_client, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes_internal.settings.cron_shared_secret", "the-real-secret"
        )
        monkeypatch.setattr(
            "app.api.routes_internal.settings.encryption_key", Fernet.generate_key().decode()
        )
        monkeypatch.setattr("app.api.routes_internal.settings.encryption_key_version", 1)

        response = await api_client.post(
            "/internal/tick", headers={"X-Cron-Secret": "the-real-secret"}
        )
        assert response.status_code == 200
        assert response.json() == {"reminders_sent": 0, "processed_events_deleted": 0}

    async def test_counts_actual_due_reminders(self, api_client, db_session, monkeypatch):
        """This test ensures the endpoint actually counts real due reminders,
        not just returning a hardcoded 0. A stub that always returns 0 would
        fail this test."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)

        monkeypatch.setattr(
            "app.api.routes_internal.settings.cron_shared_secret", "the-real-secret"
        )
        monkeypatch.setattr(
            "app.api.routes_internal.settings.encryption_key", key
        )
        monkeypatch.setattr("app.api.routes_internal.settings.encryption_key_version", 1)

        await _make_workspace(db_session, "team-A", cipher)

        # Create a task and a due reminder
        from app.models import Reminder, Task

        task = Task(
            team_id="team-A",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title="Test task",
            due_at_utc=datetime.now(UTC),
            channel_id="C1",
        )
        db_session.add(task)
        await db_session.flush()

        reminder = Reminder(
            task_id=task.id,
            team_id="team-A",
            fire_at_utc=datetime.now(UTC),
            escalation_level=0,
        )
        db_session.add(reminder)
        await db_session.commit()

        # Mock the Slack client to avoid actual API calls
        class FakeSlackClient:
            async def conversations_open(self, users):
                return {"channel": {"id": "D1"}}

            async def chat_postMessage(self, channel, text):
                pass

        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace",
            lambda workspace, cipher: FakeSlackClient(),
        )
        monkeypatch.setattr(
            "app.api.routes_internal.token_cipher_from_settings", lambda settings: cipher
        )

        response = await api_client.post(
            "/internal/tick", headers={"X-Cron-Secret": "the-real-secret"}
        )
        assert response.status_code == 200
        assert response.json() == {"reminders_sent": 1, "processed_events_deleted": 0}
