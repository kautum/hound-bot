from datetime import UTC, datetime, timedelta

from app.models import Meeting, Workspace
from app.services.analytics_service import (
    build_weekly_digest,
    meeting_count_since,
    overdue_task_count,
    task_completion_rate,
)
from app.services.task_service import create_task, mark_task_done


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


class TestTaskCompletionRate:
    async def test_returns_1_when_no_tasks_in_window(self, db_session):
        await _make_workspace(db_session, "team-A")
        since = datetime.now(UTC) - timedelta(days=7)
        rate = await task_completion_rate(db_session, "team-A", since)
        assert rate == 1.0

    async def test_computes_the_real_fraction(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        done_task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title="Done one",
            due_at_utc=due,
            channel_id="C1",
        )
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title="Still open",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()
        await mark_task_done(
            db_session, team_id="team-A", task_id=done_task.id, requesting_slack_user_id="U1"
        )
        await db_session.commit()

        since = datetime.now(UTC) - timedelta(days=7)
        rate = await task_completion_rate(db_session, "team-A", since)
        assert rate == 0.5

    async def test_excludes_tasks_created_before_the_window(self, db_session):
        """The exact bug this test exists to catch: a query that claims to
        be scoped to a window but silently ignores it."""
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title="Old task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        future_since = datetime.now(UTC) + timedelta(days=1)  # window starts after the task existed
        rate = await task_completion_rate(db_session, "team-A", future_since)
        assert rate == 1.0  # zero tasks *in the window* — not "one task, incomplete"


class TestOverdueTaskCount:
    async def test_counts_only_open_tasks_past_their_due_date(self, db_session):
        await _make_workspace(db_session, "team-A")
        past_due = datetime.now(UTC) - timedelta(days=1)
        future_due = datetime.now(UTC) + timedelta(days=1)

        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title="Overdue",
            due_at_utc=past_due,
            channel_id="C1",
        )
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title="Not due yet",
            due_at_utc=future_due,
            channel_id="C1",
        )
        await db_session.commit()

        assert await overdue_task_count(db_session, "team-A") == 1


class TestMeetingCountSince:
    async def test_counts_meetings_created_within_the_window(self, db_session):
        await _make_workspace(db_session, "team-A")
        db_session.add(
            Meeting(team_id="team-A", organiser_slack_id="U1", duration_min=30)
        )
        await db_session.commit()

        since = datetime.now(UTC) - timedelta(days=7)
        assert await meeting_count_since(db_session, "team-A", since) == 1

        future_since = datetime.now(UTC) + timedelta(days=1)
        assert await meeting_count_since(db_session, "team-A", future_since) == 0


class TestBuildWeeklyDigest:
    async def test_produces_a_readable_summary(self, db_session):
        await _make_workspace(db_session, "team-A")
        text = await build_weekly_digest(db_session, "team-A")
        assert "Task completion rate" in text
        assert "Overdue tasks" in text
        assert "Meetings this week" in text
