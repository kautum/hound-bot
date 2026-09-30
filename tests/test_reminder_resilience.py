"""One bad reminder must never block the others or fail the tick.

Slack's SDK talks over aiohttp (not httpx), so respx cannot intercept it. Instead
these tests run a real local aiohttp server that speaks just enough of the Slack
Web API, and point a real `AsyncWebClient` at it. That exercises the real
`SlackApiError` shapes (ok:false bodies, HTTP 429, HTTP 500, read timeouts).
"""

import asyncio
import logging
import traceback
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.fernet import Fernet
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import TokenCipher
from app.models import Reminder, Task, Workspace
from app.repositories.reminder_repository import RETRY_BACKOFF_SECONDS
from app.scheduler import process_due_reminders
from app.services.task_service import create_task

CLIENT_TIMEOUT_SECONDS = 1
SERVER_SLOW_SECONDS = 3


async def _payload(request: web.Request) -> dict:
    """The SDK sends some arguments as query params, some as JSON or form."""
    data: dict = dict(request.query)
    if request.content_type == "application/json":
        data.update(await request.json())
    else:
        data.update(await request.post())
    return data


class FakeSlackServer:
    """Behaviour is keyed on the `users` value of conversations.open."""

    def __init__(self):
        self.behavior: dict[str, str] = {}
        self.opens: Counter[str] = Counter()
        self.posts: Counter[str] = Counter()
        self.url = ""

    async def conversations_open(self, request: web.Request) -> web.Response:
        user = str((await _payload(request))["users"])
        self.opens[user] += 1
        mode = self.behavior.get(user, "ok")
        if mode == "ok":
            return web.json_response({"ok": True, "channel": {"id": f"D-{user}"}})
        if mode == "http500":
            return web.json_response({"ok": False, "error": "boom"}, status=500)
        if mode == "ratelimited":
            return web.json_response(
                {"ok": False, "error": "ratelimited"}, status=429, headers={"Retry-After": "1"}
            )
        if mode == "timeout":
            await asyncio.sleep(SERVER_SLOW_SECONDS)
            return web.json_response({"ok": True, "channel": {"id": f"D-{user}"}})
        return web.json_response({"ok": False, "error": mode})

    async def chat_post_message(self, request: web.Request) -> web.Response:
        self.posts[str((await _payload(request))["channel"])] += 1
        return web.json_response({"ok": True})


@pytest.fixture
async def slack_server(monkeypatch):
    fake = FakeSlackServer()
    app = web.Application()
    app.router.add_post("/conversations.open", fake.conversations_open)
    app.router.add_post("/chat.postMessage", fake.chat_post_message)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("/"))

    def build_client(workspace, cipher):
        # Same decrypt step as the real builder (raises ValueError on a bad
        # token), but the client talks to the local fake server.
        token = cipher.decrypt(workspace.bot_token_enc, workspace.key_version)
        return AsyncWebClient(token=token, base_url=fake.url, timeout=CLIENT_TIMEOUT_SECONDS)

    monkeypatch.setattr("app.scheduler.build_client_for_workspace", build_client)
    yield fake
    await server.close()


@pytest.fixture
def cipher() -> TokenCipher:
    return TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)


async def _workspace(session, team_id: str, cipher: TokenCipher) -> None:
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


async def _due_task(session, team_id: str, assignee: str, minutes_ago: int) -> Task:
    """One task whose single due-today reminder is already due. Larger
    minutes_ago means an earlier fire time, so it is claimed first."""
    return await create_task(
        session,
        team_id=team_id,
        creator_slack_id="U_CREATOR",
        assignee_slack_id=assignee,
        title=f"task for {assignee}",
        due_at_utc=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        channel_id="C1",
    )


async def _reminder_for(session, task: Task) -> Reminder:
    # _due_task makes a past-due task: only the due-today (level 1) reminder is due.
    result = await session.execute(
        select(Reminder).where(Reminder.task_id == task.id, Reminder.escalation_level == 1)
    )
    reminder = result.scalar_one()
    await session.refresh(reminder)
    return reminder


class TestPermanentFailures:
    async def test_bad_assignee_is_marked_sent_and_does_not_block_good_reminders(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        bad = await _due_task(db_session, "team-A", "ZZZZ", minutes_ago=300)
        good1 = await _due_task(db_session, "team-A", "U_GOOD1", minutes_ago=200)
        good2 = await _due_task(db_session, "team-A", "U_GOOD2", minutes_ago=100)
        await db_session.commit()
        slack_server.behavior["ZZZZ"] = "user_not_found"

        sent = await process_due_reminders(db_session, cipher)

        assert sent == 2
        assert slack_server.posts["D-U_GOOD1"] == 1
        assert slack_server.posts["D-U_GOOD2"] == 1
        assert slack_server.posts["D-ZZZZ"] == 0
        for task in (bad, good1, good2):
            assert (await _reminder_for(db_session, task)).sent_at is not None

        # Next tick: nothing left, bad one is never retried.
        assert await process_due_reminders(db_session, cipher) == 0
        assert slack_server.opens["ZZZZ"] == 1

    @pytest.mark.parametrize(
        "code",
        [
            "user_not_found", "channel_not_found", "is_archived", "account_inactive",
            "user_disabled", "token_revoked", "token_expired", "invalid_auth", "not_authed",
            "cannot_dm_bot", "not_in_channel", "missing_scope", "invalid_blocks",
            "msg_too_long",
        ],
    )  # fmt: skip
    async def test_every_permanent_code_marks_sent_and_continues(
        self, db_session, cipher, monkeypatch, code
    ):
        await _workspace(db_session, "team-A", cipher)
        bad = await _due_task(db_session, "team-A", "U_BAD", minutes_ago=300)
        good = await _due_task(db_session, "team-A", "U_GOOD", minutes_ago=100)
        await db_session.commit()
        posted: list[str] = []

        class Client:
            async def conversations_open(self, users):
                if users == "U_BAD":
                    raise SlackApiError("failed", {"ok": False, "error": code})
                return {"channel": {"id": f"D-{users}"}}

            async def chat_postMessage(self, channel, text, blocks=None):
                posted.append(channel)

        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace", lambda workspace, cipher: Client()
        )

        assert await process_due_reminders(db_session, cipher) == 1
        assert posted == ["D-U_GOOD"]
        assert (await _reminder_for(db_session, bad)).sent_at is not None
        assert (await _reminder_for(db_session, good)).sent_at is not None

    async def test_decrypt_failure_for_one_workspace_does_not_stop_another(
        self, db_session, slack_server, cipher
    ):
        other_cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)
        await _workspace(db_session, "team-BAD", other_cipher)  # undecryptable with `cipher`
        await _workspace(db_session, "team-A", cipher)
        bad = await _due_task(db_session, "team-BAD", "U_BADWS", minutes_ago=300)
        good = await _due_task(db_session, "team-A", "U_GOOD", minutes_ago=100)
        await db_session.commit()

        sent = await process_due_reminders(db_session, cipher)

        assert sent == 1
        assert slack_server.posts["D-U_GOOD"] == 1
        assert (await _reminder_for(db_session, bad)).sent_at is not None
        assert (await _reminder_for(db_session, good)).sent_at is not None

    async def test_warning_logs_error_code_but_never_the_token(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        await _due_task(db_session, "team-A", "ZZZZ", minutes_ago=10)
        await db_session.commit()
        slack_server.behavior["ZZZZ"] = "user_not_found"

        # Attach a handler directly: alembic's fileConfig (run by the migration
        # tests) disables pre-existing loggers, which would blind caplog here.
        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[method-assign]
        scheduler_logger = logging.getLogger("app.scheduler")
        was_disabled = scheduler_logger.disabled
        scheduler_logger.disabled = False
        scheduler_logger.addHandler(handler)
        try:
            await process_due_reminders(db_session, cipher)
        finally:
            scheduler_logger.removeHandler(handler)
            scheduler_logger.disabled = was_disabled

        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        record = warnings[0]
        assert record.exc_info is not None
        rendered = record.getMessage() + "".join(traceback.format_exception(*record.exc_info))
        assert "user_not_found" in rendered
        assert "xoxb-fake" not in rendered


class TestTransientFailures:
    @pytest.mark.parametrize("mode", ["http500", "timeout", "ratelimited"])
    async def test_transient_failure_releases_claim_and_is_retried_next_tick(
        self, db_session, slack_server, cipher, mode
    ):
        await _workspace(db_session, "team-A", cipher)
        flaky = await _due_task(db_session, "team-A", "U_FLAKY", minutes_ago=300)
        good = await _due_task(db_session, "team-A", "U_GOOD", minutes_ago=100)
        await db_session.commit()
        slack_server.behavior["U_FLAKY"] = mode

        sent = await process_due_reminders(db_session, cipher)

        assert sent == 1
        assert slack_server.posts["D-U_GOOD"] == 1
        flaky_row = await _reminder_for(db_session, flaky)
        assert flaky_row.sent_at is None
        assert flaky_row.claimed_at is None
        assert (await _reminder_for(db_session, good)).sent_at is not None

        # The retry is backed off (see TestNoStarvation); let the backoff elapse.
        flaky_row.fire_at_utc = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()
        slack_server.behavior["U_FLAKY"] = "ok"
        assert await process_due_reminders(db_session, cipher) == 1
        assert slack_server.posts["D-U_FLAKY"] == 1
        assert (await _reminder_for(db_session, flaky)).sent_at is not None

    async def test_unexpected_exception_is_transient(self, db_session, cipher, monkeypatch):
        await _workspace(db_session, "team-A", cipher)
        flaky = await _due_task(db_session, "team-A", "U_FLAKY", minutes_ago=300)
        good = await _due_task(db_session, "team-A", "U_GOOD", minutes_ago=100)
        await db_session.commit()

        class Client:
            async def conversations_open(self, users):
                if users == "U_FLAKY":
                    raise RuntimeError("something odd")
                return {"channel": {"id": f"D-{users}"}}

            async def chat_postMessage(self, channel, text, blocks=None):
                return None

        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace", lambda workspace, cipher: Client()
        )

        assert await process_due_reminders(db_session, cipher) == 1
        flaky_row = await _reminder_for(db_session, flaky)
        assert flaky_row.sent_at is None and flaky_row.claimed_at is None
        assert (await _reminder_for(db_session, good)).sent_at is not None


class TestNoStarvation:
    async def test_persistently_failing_reminders_cannot_starve_a_healthy_later_one(
        self, db_session, slack_server, cipher
    ):
        """10 reminders that always fail transiently used to be re-released at
        the head of `ORDER BY fire_at_utc LIMIT 10` forever, so a healthy
        reminder that fired later was never reached."""
        await _workspace(db_session, "team-A", cipher)
        flaky_users = [f"U_FLAKY{i:02d}" for i in range(10)]
        for i, user in enumerate(flaky_users):
            await _due_task(db_session, "team-A", user, minutes_ago=500 + i)
            slack_server.behavior[user] = "ratelimited"
        healthy = await _due_task(db_session, "team-A", "U_HEALTHY", minutes_ago=10)
        await db_session.commit()

        for _ in range(3):
            await process_due_reminders(db_session, cipher)

        assert slack_server.posts["D-U_HEALTHY"] == 1
        assert (await _reminder_for(db_session, healthy)).sent_at is not None
        # The failing ones were attempted once (first tick) and backed off, not
        # hammered on every tick, and are still unsent.
        for user in flaky_users:
            assert slack_server.opens[user] == 1, user
        assert slack_server.posts.keys() == {"D-U_HEALTHY"}

    async def test_released_reminder_is_backed_off_then_retried_when_due(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        flaky = await _due_task(db_session, "team-A", "U_FLAKY", minutes_ago=300)
        await db_session.commit()
        slack_server.behavior["U_FLAKY"] = "http500"

        before = datetime.now(UTC)
        await process_due_reminders(db_session, cipher)
        row = await _reminder_for(db_session, flaky)
        assert row.sent_at is None and row.claimed_at is None
        assert row.fire_at_utc >= before + timedelta(seconds=RETRY_BACKOFF_SECONDS - 5)

        # Not retried while backed off, even though it is unsent.
        assert await process_due_reminders(db_session, cipher) == 0
        assert slack_server.opens["U_FLAKY"] == 1

        # Once the backoff elapses it is retried and delivered.
        row.fire_at_utc = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()
        slack_server.behavior["U_FLAKY"] = "ok"
        assert await process_due_reminders(db_session, cipher) == 1
        assert slack_server.opens["U_FLAKY"] == 2
        assert (await _reminder_for(db_session, flaky)).sent_at is not None


async def _overdue_task(session, assignee: str, creator: str) -> Task:
    """Overdue for 25h: the overdue (level 2) reminder notifies assignee then creator."""
    return await create_task(
        session,
        team_id="team-A",
        creator_slack_id=creator,
        assignee_slack_id=assignee,
        title="overdue thing",
        due_at_utc=datetime.now(UTC) - timedelta(hours=25),
        channel_id="C1",
    )


async def _overdue_reminder(session, task: Task) -> Reminder:
    result = await session.execute(
        select(Reminder).where(Reminder.task_id == task.id, Reminder.escalation_level == 2)
    )
    reminder = result.scalar_one()
    await session.refresh(reminder)
    return reminder


class TestPartialDelivery:
    async def test_assignee_ok_creator_transient_failure_is_not_retried(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        task = await _overdue_task(db_session, "U_ASSIGNEE", "U_CREATOR")
        await db_session.commit()
        slack_server.behavior["U_CREATOR"] = "http500"

        await process_due_reminders(db_session, cipher)
        await process_due_reminders(db_session, cipher)

        # Exactly one DM to the assignee for the overdue reminder across both
        # ticks (the other assignee DMs come from the level-1 reminder).
        overdue = await _overdue_reminder(db_session, task)
        assert overdue.sent_at is not None
        assert slack_server.opens["U_CREATOR"] == 1
        assert slack_server.posts["D-U_ASSIGNEE"] == 2  # level 1 + level 2, once each

    async def test_assignee_equal_to_creator_gets_one_dm_per_reminder(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        task = await _overdue_task(db_session, "U_SAME", "U_SAME")
        await db_session.commit()

        assert await process_due_reminders(db_session, cipher) == 2

        # A 25h-overdue task has two due reminders (due-today and overdue), each
        # DMing the one person once: 2 opens and 2 posts in total, not 3.
        assert slack_server.opens["U_SAME"] == 2
        assert slack_server.posts["D-U_SAME"] == 2
        assert (await _overdue_reminder(db_session, task)).sent_at is not None

    async def test_assignee_is_dmed_before_creator(self, db_session, cipher, monkeypatch):
        await _workspace(db_session, "team-A", cipher)
        await _overdue_task(db_session, "U_ASSIGNEE", "U_CREATOR")
        await db_session.commit()
        order: list[str] = []

        class Client:
            async def conversations_open(self, users):
                order.append(users)
                return {"channel": {"id": f"D-{users}"}}

            async def chat_postMessage(self, channel, text, blocks=None):
                return None

        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace", lambda workspace, cipher: Client()
        )
        await process_due_reminders(db_session, cipher)
        assert order == ["U_ASSIGNEE", "U_ASSIGNEE", "U_CREATOR"]

    async def test_creator_ok_assignee_permanent_failure_marks_sent(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        task = await _overdue_task(db_session, "U_ASSIGNEE", "U_CREATOR")
        await db_session.commit()
        slack_server.behavior["U_ASSIGNEE"] = "user_not_found"

        await process_due_reminders(db_session, cipher)
        await process_due_reminders(db_session, cipher)

        assert (await _overdue_reminder(db_session, task)).sent_at is not None
        assert slack_server.posts["D-U_CREATOR"] == 1
        assert slack_server.opens["U_CREATOR"] == 1

    async def test_both_fail_transiently_keeps_existing_retry_behaviour(
        self, db_session, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        task = await _overdue_task(db_session, "U_ASSIGNEE", "U_CREATOR")
        await db_session.commit()
        slack_server.behavior["U_ASSIGNEE"] = "http500"
        slack_server.behavior["U_CREATOR"] = "http500"

        await process_due_reminders(db_session, cipher)

        overdue = await _overdue_reminder(db_session, task)
        assert overdue.sent_at is None and overdue.claimed_at is None
        assert slack_server.posts == Counter()

    async def test_both_fail_permanently_marks_sent(self, db_session, slack_server, cipher):
        await _workspace(db_session, "team-A", cipher)
        task = await _overdue_task(db_session, "U_ASSIGNEE", "U_CREATOR")
        await db_session.commit()
        slack_server.behavior["U_ASSIGNEE"] = "user_not_found"
        slack_server.behavior["U_CREATOR"] = "user_not_found"

        assert await process_due_reminders(db_session, cipher) == 0
        assert (await _overdue_reminder(db_session, task)).sent_at is not None


class TestConcurrentTicks:
    async def test_twenty_concurrent_ticks_send_each_reminder_exactly_once(
        self, db_session, db_engine, slack_server, cipher
    ):
        await _workspace(db_session, "team-A", cipher)
        users = [f"U_{i:02d}" for i in range(12)]
        for i, user in enumerate(users):
            await _due_task(db_session, "team-A", user, minutes_ago=100 + i)
        await db_session.commit()

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

        async def tick() -> int:
            async with session_factory() as session:
                return await process_due_reminders(session, cipher)

        results = await asyncio.gather(*(tick() for _ in range(20)))

        assert sum(results) == len(users)
        for user in users:
            assert slack_server.opens[user] == 1, user
            assert slack_server.posts[f"D-{user}"] == 1, user


class TestTickEndpoint:
    async def test_bad_reminder_does_not_fail_the_tick(
        self, api_client, db_session, slack_server, cipher, monkeypatch
    ):
        """The original bug: one reminder with `user_not_found` made
        /internal/tick return 500 on every call, forever."""
        monkeypatch.setattr("app.api.routes_internal.settings.cron_shared_secret", "s3cret")
        monkeypatch.setattr(
            "app.api.routes_internal.token_cipher_from_settings", lambda settings: cipher
        )
        await _workspace(db_session, "team-A", cipher)
        bad = await _due_task(db_session, "team-A", "ZZZZ", minutes_ago=300)
        await _due_task(db_session, "team-A", "U_GOOD1", minutes_ago=200)
        await _due_task(db_session, "team-A", "U_GOOD2", minutes_ago=100)
        await db_session.commit()
        slack_server.behavior["ZZZZ"] = "user_not_found"

        response = await api_client.post("/internal/tick", headers={"X-Cron-Secret": "s3cret"})

        assert response.status_code == 200
        assert response.json()["reminders_sent"] == 2
        assert set(response.json()) == {"reminders_sent", "processed_events_deleted"}
        assert slack_server.posts["D-U_GOOD1"] == 1
        assert slack_server.posts["D-U_GOOD2"] == 1
        assert (await _reminder_for(db_session, bad)).sent_at is not None

        again = await api_client.post("/internal/tick", headers={"X-Cron-Secret": "s3cret"})
        assert again.status_code == 200
        assert again.json()["reminders_sent"] == 0
        assert slack_server.opens["ZZZZ"] == 1
