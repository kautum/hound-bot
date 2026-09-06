from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet

from app.models import Workspace
from app.scheduler import process_due_reminders
from app.services.task_service import create_task


class FakeSlackClient:
    def __init__(self):
        self.dm_opens: list[str] = []
        self.posted: list[tuple[str, str]] = []

    async def conversations_open(self, users: str):
        self.dm_opens.append(users)
        return {"channel": {"id": f"DM-{users}"}}

    async def chat_postMessage(self, channel: str, text: str):
        self.posted.append((channel, text))


async def _make_workspace_with_real_token(session, team_id: str, cipher) -> None:
    token_enc, version = cipher.encrypt("xoxb-fake")
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=token_enc,
            key_version=version,
            installed_at=datetime.now(UTC),
        )
    )
    await session.flush()


class TestProcessDueReminders:
    async def test_overdue_reminder_notifies_both_assignee_and_creator(
        self, db_session, monkeypatch
    ):
        from app.core.security import TokenCipher

        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        # Due 25h ago, so create_task's overdue reminder (+24h) is already due now.
        due = datetime.now(UTC) - timedelta(hours=25)
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Overdue thing",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        sent = await process_due_reminders(db_session, cipher)

        # Due 25h ago means both the "due today" (fires at due_at) and the
        # "overdue" (fires at due_at + 24h) reminders are already past due —
        # only "upcoming" (due_at - 24h, i.e. 49h ago) collapses into the
        # same claim. Both get processed in one batch.
        assert sent == 2
        assert fake_client.dm_opens.count("U_ASSIGNEE") == 2  # notified by both reminders
        assert fake_client.dm_opens.count("U_CREATOR") == 1  # only the overdue one notifies them
        assert len(fake_client.posted) == 3

    async def test_uninstalled_workspace_reminders_are_marked_sent_without_posting(
        self, db_session, monkeypatch
    ):
        from app.core.security import TokenCipher

        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        due = datetime.now(UTC) - timedelta(hours=25)
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Thing",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        from app.repositories.workspace_repository import WorkspaceRepository

        await WorkspaceRepository(db_session).mark_uninstalled("team-A")
        await db_session.commit()

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        await process_due_reminders(db_session, cipher)
        assert fake_client.posted == []
