import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import respx
from cryptography.fernet import Fernet

from app.agent.loop import GROQ_CHAT_URL
from app.calendar.google_calendar import GOOGLE_FREEBUSY_URL, GOOGLE_TOKEN_URL
from app.core.security import TokenCipher
from app.models import InboundJob, Task, User, Workspace
from app.worker import (
    _strip_bot_mention,
    handle_app_home_opened,
    handle_app_mention,
    handle_meet_book,
    handle_meet_cancel,
    handle_meet_propose,
)


class FakeSlackClient:
    def __init__(self):
        self.posted: list[tuple[str, str]] = []
        self.published_views: list[tuple[str, dict]] = []

    async def chat_postMessage(self, channel: str, text: str):
        self.posted.append((channel, text))

    async def views_publish(self, user_id: str, view: dict):
        self.published_views.append((user_id, view))


def _text_response(text: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": text}}]}
    )


def _make_job(team_id: str, text: str, user: str = "U1", channel: str = "C1") -> InboundJob:
    return InboundJob(
        team_id=team_id,
        event_type="app_mention",
        payload={"channel": channel, "text": text, "user": user},
        status="pending",
        created_at=datetime.now(UTC),
    )


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
    await session.commit()


class TestStripBotMention:
    def test_strips_a_leading_mention(self):
        assert _strip_bot_mention("<@U0BOT|assistant> do the thing") == "do the thing"

    def test_leaves_text_with_no_leading_mention_alone(self):
        assert _strip_bot_mention("do the thing") == "do the thing"


class TestHandleAppMention:
    async def test_posts_a_clear_message_when_groq_is_not_configured(
        self, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        monkeypatch.setattr("app.worker.settings.groq_api_key", None)
        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        job = _make_job("team-A", "<@U0BOT> remind me to do X")
        await handle_app_mention(db_session, job, cipher)

        assert len(fake_client.posted) == 1
        assert "set up" in fake_client.posted[0][1]

    async def test_uninstalled_workspace_does_nothing(self, db_session, monkeypatch):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        from app.repositories.workspace_repository import WorkspaceRepository

        await WorkspaceRepository(db_session).mark_uninstalled("team-A")
        await db_session.commit()

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        job = _make_job("team-A", "<@U0BOT> hello")
        await handle_app_mention(db_session, job, cipher)

        assert fake_client.posted == []

    @respx.mock
    async def test_real_mention_goes_through_the_agent_and_creates_a_task(
        self, db_session, monkeypatch
    ):
        """The exact gap this test exists to close: a mention must actually
        reach the LLM agent loop, not just get a canned reply."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        monkeypatch.setattr("app.worker.settings.groq_api_key", "fake-groq-key")
        monkeypatch.setattr("app.worker.settings.google_client_id", None)
        monkeypatch.setattr("app.worker.settings.google_client_secret", None)

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        route = respx.post(GROQ_CHAT_URL)
        route.side_effect = [
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "create_task",
                                            "arguments": json.dumps(
                                                {
                                                    "assignee_slack_id": "U1",
                                                    "title": "File the report",
                                                    "due_at_utc": "2026-09-10T17:00:00+00:00",
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            ),
            _text_response("Done — created that task."),
        ]

        job = _make_job("team-A", "<@U0BOT> remind me to file the report friday at 5pm UTC")
        await handle_app_mention(db_session, job, cipher)

        assert fake_client.posted == [("C1", "Done — created that task.")]

        from app.services.task_service import list_open_tasks

        tasks = await list_open_tasks(db_session, team_id="team-A", assignee_slack_id="U1")
        assert len(tasks) == 1
        assert tasks[0].title == "File the report"


def _make_meet_job(team_id: str, payload: dict) -> InboundJob:
    return InboundJob(
        team_id=team_id,
        event_type="meet_propose",
        payload=payload,
        status="pending",
        created_at=datetime.now(UTC),
    )


class TestHandleMeetPropose:
    """The exact fix this closes: propose used to run inline in the slash
    command handler, one real Google API call per participant — a direct
    violation of ARCHITECTURE.md's 3-second rule. It's now a queued job."""

    @respx.mock
    async def test_posts_to_response_url_when_not_configured(self, monkeypatch, db_session):
        monkeypatch.setattr("app.worker.settings.google_client_id", None)

        response_url_route = respx.post("https://hooks.slack.invalid/fake").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        job = _make_meet_job(
            "team-A",
            {
                "organiser_id": "U1",
                "participant_slack_ids": ["U2"],
                "duration_minutes": 30,
                "window_start_utc": "2026-09-07T09:00:00+00:00",
                "window_end_utc": "2026-09-07T17:00:00+00:00",
                "response_url": "https://hooks.slack.invalid/fake",
            },
        )
        cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)
        await handle_meet_propose(db_session, job, cipher)

        sent = json.loads(response_url_route.calls.last.request.content)
        assert "isn't configured" in sent["text"]

    @respx.mock
    async def test_finds_a_slot_and_posts_the_result(self, monkeypatch, db_session):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        token_enc, version = cipher.encrypt("tok-alice")

        await _make_workspace_with_real_token(db_session, "team-A", cipher)
        db_session.add(
            User(
                slack_user_id="U1",
                team_id="team-A",
                tz="UTC",
                google_refresh_token_enc=token_enc,
                key_version=version,
                google_email="a@x.com",
            )
        )
        await db_session.commit()

        monkeypatch.setattr("app.worker.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.worker.settings.google_client_secret", "gsecret")

        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "ya29.fake"})
        )
        respx.post(GOOGLE_FREEBUSY_URL).mock(
            return_value=httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})
        )
        response_url_route = respx.post("https://hooks.slack.invalid/fake").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        job = _make_meet_job(
            "team-A",
            {
                "organiser_id": "U1",
                "participant_slack_ids": [],
                "duration_minutes": 30,
                "window_start_utc": "2026-09-07T09:00:00+00:00",
                "window_end_utc": "2026-09-07T17:00:00+00:00",
                "response_url": "https://hooks.slack.invalid/fake",
            },
        )
        await handle_meet_propose(db_session, job, cipher)

        sent = json.loads(response_url_route.calls.last.request.content)
        assert "Earliest mutual slot" in sent["text"]
        assert "2026-09-07T09:00:00+00:00" in sent["text"]
        assert "blocks" in sent
        assert len(sent["blocks"]) == 2
        assert sent["blocks"][0]["type"] == "section"
        assert sent["blocks"][1]["type"] == "actions"
        assert sent["blocks"][1]["elements"][0]["action_id"] == "meet_book"
        assert sent["blocks"][1]["elements"][0]["text"]["text"] == "Book"

    async def test_missing_response_url_does_not_crash(self, monkeypatch, db_session):
        monkeypatch.setattr("app.worker.settings.google_client_id", None)
        job = _make_meet_job(
            "team-A",
            {
                "organiser_id": "U1",
                "participant_slack_ids": ["U2"],
                "duration_minutes": 30,
                "window_start_utc": "2026-09-07T09:00:00+00:00",
                "window_end_utc": "2026-09-07T17:00:00+00:00",
                "response_url": "",
            },
        )
        cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)

        with respx.mock(assert_all_called=False) as mock_router:
            # No routes registered — the real gap this test guards is that
            # _respond's `if not response_url: return` guard actually fires
            # before any HTTP call is attempted. Without that guard, an
            # empty response_url passed straight to httpx.post would raise
            # (invalid URL), which is exactly the crash this test's name
            # claims doesn't happen — but the old body never checked that a
            # crash was even possible in the first place.
            await handle_meet_propose(db_session, job, cipher)
            assert len(mock_router.calls) == 0


class TestHandleMeetBook:
    """Test that handle_meet_book includes a Cancel button in its response."""

    @respx.mock
    async def test_books_and_includes_cancel_button(self, monkeypatch, db_session):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        token_enc, version = cipher.encrypt("tok-alice")

        await _make_workspace_with_real_token(db_session, "team-A", cipher)
        db_session.add(
            User(
                slack_user_id="U1",
                team_id="team-A",
                tz="UTC",
                google_refresh_token_enc=token_enc,
                key_version=version,
                google_email="a@x.com",
            )
        )
        await db_session.commit()

        monkeypatch.setattr("app.worker.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.worker.settings.google_client_secret", "gsecret")

        from app.calendar.google_calendar import GOOGLE_EVENTS_URL, GOOGLE_TOKEN_URL

        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "ya29.fake"})
        )
        respx.post(GOOGLE_EVENTS_URL).mock(
            return_value=httpx.Response(200, json={"id": "evt_123"})
        )
        response_url_route = respx.post("https://hooks.slack.invalid/fake").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        from app.models.meeting import STATUS_PROPOSED, Meeting, MeetingParticipant

        meeting_id = uuid.uuid4()
        db_session.add(
            Meeting(
                id=meeting_id,
                team_id="team-A",
                organiser_slack_id="U1",
                duration_min=30,
                status=STATUS_PROPOSED,
                proposed_start_utc=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db_session.add(
            MeetingParticipant(
                meeting_id=meeting_id,
                team_id="team-A",
                slack_user_id="U1",
            )
        )
        await db_session.commit()

        job = InboundJob(
            team_id="team-A",
            event_type="meet_book",
            payload={
                "meeting_id": str(meeting_id),
                "requesting_user_id": "U1",
                "response_url": "https://hooks.slack.invalid/fake",
            },
            status="pending",
            created_at=datetime.now(UTC),
        )

        await handle_meet_book(db_session, job, cipher)

        sent = json.loads(response_url_route.calls.last.request.content)
        assert "Booked" in sent["text"]
        assert "blocks" in sent
        assert len(sent["blocks"]) == 2
        assert sent["blocks"][0]["type"] == "section"
        assert sent["blocks"][1]["type"] == "actions"
        assert sent["blocks"][1]["elements"][0]["action_id"] == "meet_cancel"
        assert sent["blocks"][1]["elements"][0]["text"]["text"] == "Cancel"


class TestWorkerSupervision:
    """S7: the worker loop used to have no supervision — one raised
    exception from process_one_batch ended it permanently, silently, with
    /health none the wiser."""

    async def test_worker_is_alive_false_before_any_poll(self):
        import app.worker as worker_module

        worker_module._last_poll_completed_utc = None
        assert worker_module.worker_is_alive() is False

    async def test_worker_is_alive_true_after_a_recent_poll(self):
        import app.worker as worker_module

        worker_module._last_poll_completed_utc = datetime.now(UTC)
        assert worker_module.worker_is_alive() is True

    async def test_worker_is_alive_false_once_stale(self):
        from datetime import timedelta

        import app.worker as worker_module

        worker_module._last_poll_completed_utc = datetime.now(UTC) - timedelta(
            seconds=worker_module.STALE_AFTER_SECONDS + 1
        )
        assert worker_module.worker_is_alive() is False

    async def test_loop_survives_a_failing_iteration_and_keeps_polling(self, monkeypatch):
        """Reproduces S7 directly: process_one_batch raises on the first
        call. The old code let that exception end the while loop forever;
        the fix must catch it, record a poll timestamp anyway, and continue
        to a second iteration."""
        import asyncio

        import app.worker as worker_module

        worker_module._last_poll_completed_utc = None
        call_count = 0

        async def _flaky_process_one_batch(session, cipher):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated DB blip")
            raise asyncio.CancelledError()  # stop the loop cleanly after iteration 2

        monkeypatch.setattr(worker_module, "process_one_batch", _flaky_process_one_batch)
        monkeypatch.setattr(worker_module, "token_cipher_from_settings", lambda settings: object())
        monkeypatch.setattr(worker_module, "POLL_INTERVAL_SECONDS", 0)

        class _FakeSession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(worker_module, "async_session_factory", lambda: _FakeSession())

        try:
            await worker_module.run_worker_loop()
        except asyncio.CancelledError:
            pass

        # Reached iteration 2 despite iteration 1 raising — the loop did not die.
        assert call_count == 2
        assert worker_module.worker_is_alive() is True


class TestUnknownEventTypeHandling:
    """S13: jobs with unknown event_type must be marked failed, not done."""

    async def test_unknown_event_type_is_marked_failed_with_error(
        self, db_session, monkeypatch
    ):
        """When a job has an event_type not in HANDLERS, it should be marked
        failed with a clear error message, not silently marked done."""
        from cryptography.fernet import Fernet
        from sqlalchemy import select

        from app.core.security import TokenCipher
        from app.worker import process_one_batch

        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)

        # Create a workspace first (required by foreign key constraint)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        # Create a job with an unknown event_type
        job = InboundJob(
            team_id="team-A",
            event_type="unknown_event_type",
            payload={},
            status="pending",
            created_at=datetime.now(UTC),
        )
        db_session.add(job)
        await db_session.commit()

        # Process the batch
        await process_one_batch(db_session, cipher)

        # Verify the job is marked failed with a non-null error
        result = await db_session.execute(select(InboundJob).filter_by(id=job.id))
        updated_job = result.scalar_one_or_none()
        assert updated_job is not None
        assert updated_job.status == "failed"
        assert updated_job.error is not None
        assert "no handler registered for event_type" in updated_job.error


class TestHandleMeetCancel:
    """Cancelling a meeting mirrors handle_meet_book's structure — ack-and-enqueue
    with a response_url reply."""

    @respx.mock
    async def test_cancels_and_posts_to_response_url(self, monkeypatch, db_session):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        token_enc, version = cipher.encrypt("tok-alice")

        await _make_workspace_with_real_token(db_session, "team-A", cipher)
        db_session.add(
            User(
                slack_user_id="U1",
                team_id="team-A",
                tz="UTC",
                google_refresh_token_enc=token_enc,
                key_version=version,
                google_email="a@x.com",
            )
        )
        await db_session.commit()

        monkeypatch.setattr("app.worker.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.worker.settings.google_client_secret", "gsecret")

        from app.calendar.google_calendar import GOOGLE_EVENTS_URL, GOOGLE_TOKEN_URL

        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "ya29.fake"})
        )
        respx.delete(f"{GOOGLE_EVENTS_URL}/evt_123").mock(
            return_value=httpx.Response(200, json={})
        )
        response_url_route = respx.post("https://hooks.slack.invalid/fake").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        import uuid

        from app.models.meeting import STATUS_BOOKED, Meeting

        meeting_id = uuid.uuid4()
        db_session.add(
            Meeting(
                id=meeting_id,
                team_id="team-A",
                organiser_slack_id="U1",
                duration_min=30,
                status=STATUS_BOOKED,
                google_event_id="evt_123",
            )
        )
        await db_session.commit()

        job = InboundJob(
            team_id="team-A",
            event_type="meet_cancel",
            payload={
                "meeting_id": str(meeting_id),
                "requesting_user_id": "U1",
                "response_url": "https://hooks.slack.invalid/fake",
            },
            status="pending",
            created_at=datetime.now(UTC),
        )

        await handle_meet_cancel(db_session, job, cipher)

        sent = json.loads(response_url_route.calls.last.request.content)
        assert "Cancelled" in sent["text"]

        from sqlalchemy import select

        result = await db_session.execute(select(Meeting).filter_by(id=meeting_id))
        meeting = result.scalar_one()
        from app.models.meeting import STATUS_CANCELLED

        assert meeting.status == STATUS_CANCELLED


class TestHandleAppHomeOpened:
    """Test App Home tab handler for showing user's open tasks."""

    async def test_publishes_view_with_open_tasks(self, db_session, monkeypatch):
        """User with open tasks gets a view containing task titles and buttons."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        # Create two open tasks for the user
        db_session.add(
            Task(
                team_id="team-A",
                creator_slack_id="U1",
                assignee_slack_id="U1",
                title="Task one",
                due_at_utc=datetime.now(UTC) + timedelta(hours=24),
                status="open",
                channel_id="C1",
            )
        )
        db_session.add(
            Task(
                team_id="team-A",
                creator_slack_id="U1",
                assignee_slack_id="U1",
                title="Task two",
                due_at_utc=datetime.now(UTC) + timedelta(hours=48),
                status="open",
                channel_id="C1",
            )
        )
        await db_session.commit()

        job = InboundJob(
            team_id="team-A",
            event_type="app_home_opened",
            payload={"user": "U1"},
            status="pending",
            created_at=datetime.now(UTC),
        )

        await handle_app_home_opened(db_session, job, cipher)

        assert len(fake_client.published_views) == 1
        user_id, view = fake_client.published_views[0]
        assert user_id == "U1"
        assert view["type"] == "home"
        assert "blocks" in view

        # Check that both tasks appear with their titles
        block_texts = [
            block["text"]["text"]
            for block in view["blocks"]
            if block.get("type") == "section"
        ]
        assert any("Task one" in text for text in block_texts)
        assert any("Task two" in text for text in block_texts)

        # Check that "Mark done" buttons are present
        action_blocks = [
            block for block in view["blocks"] if block.get("type") == "actions"
        ]
        assert len(action_blocks) == 2
        for action_block in action_blocks:
            assert action_block["elements"][0]["action_id"] == "task_done"
            assert action_block["elements"][0]["text"]["text"] == "Mark done"

    async def test_shows_no_tasks_message_when_empty(self, db_session, monkeypatch):
        """User with zero open tasks gets the 'No open tasks' message."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        job = InboundJob(
            team_id="team-A",
            event_type="app_home_opened",
            payload={"user": "U1"},
            status="pending",
            created_at=datetime.now(UTC),
        )

        await handle_app_home_opened(db_session, job, cipher)

        assert len(fake_client.published_views) == 1
        user_id, view = fake_client.published_views[0]
        assert user_id == "U1"
        assert view["type"] == "home"
        assert len(view["blocks"]) == 1
        assert view["blocks"][0]["type"] == "section"
        assert "No open tasks assigned to you" in view["blocks"][0]["text"]["text"]

    async def test_tenant_isolation_tasks_from_other_team_not_shown(
        self, db_session, monkeypatch
    ):
        """View only contains tasks from the user's own team, not other teams."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)
        await _make_workspace_with_real_token(db_session, "team-B", cipher)

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        # Create task in team-A for user U1
        db_session.add(
            Task(
                team_id="team-A",
                creator_slack_id="U1",
                assignee_slack_id="U1",
                title="Team A task",
                due_at_utc=datetime.now(UTC) + timedelta(hours=24),
                status="open",
                channel_id="C1",
            )
        )
        # Create task in team-B for user U1 (should NOT appear)
        db_session.add(
            Task(
                team_id="team-B",
                creator_slack_id="U1",
                assignee_slack_id="U1",
                title="Team B task",
                due_at_utc=datetime.now(UTC) + timedelta(hours=24),
                status="open",
                channel_id="C2",
            )
        )
        await db_session.commit()

        # Open home for user in team-A
        job = InboundJob(
            team_id="team-A",
            event_type="app_home_opened",
            payload={"user": "U1"},
            status="pending",
            created_at=datetime.now(UTC),
        )

        await handle_app_home_opened(db_session, job, cipher)

        assert len(fake_client.published_views) == 1
        _, view = fake_client.published_views[0]

        block_texts = [
            block["text"]["text"]
            for block in view["blocks"]
            if block.get("type") == "section"
        ]
        assert any("Team A task" in text for text in block_texts)
        assert not any("Team B task" in text for text in block_texts)

    async def test_uninstalled_workspace_does_nothing(self, db_session, monkeypatch):
        """Uninstalled workspace should not publish any view."""
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_workspace_with_real_token(db_session, "team-A", cipher)

        from app.repositories.workspace_repository import WorkspaceRepository

        await WorkspaceRepository(db_session).mark_uninstalled("team-A")
        await db_session.commit()

        fake_client = FakeSlackClient()
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: fake_client
        )

        job = InboundJob(
            team_id="team-A",
            event_type="app_home_opened",
            payload={"user": "U1"},
            status="pending",
            created_at=datetime.now(UTC),
        )

        await handle_app_home_opened(db_session, job, cipher)

        assert len(fake_client.published_views) == 0
