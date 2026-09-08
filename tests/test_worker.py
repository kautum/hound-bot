import json
from datetime import UTC, datetime

import httpx
import respx
from cryptography.fernet import Fernet

from app.agent.loop import GROQ_CHAT_URL
from app.calendar.google_calendar import GOOGLE_FREEBUSY_URL, GOOGLE_TOKEN_URL
from app.core.security import TokenCipher
from app.models import InboundJob, User, Workspace
from app.worker import _strip_bot_mention, handle_app_mention, handle_meet_propose


class FakeSlackClient:
    def __init__(self):
        self.posted: list[tuple[str, str]] = []

    async def chat_postMessage(self, channel: str, text: str):
        self.posted.append((channel, text))


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
        await handle_meet_propose(db_session, job, cipher)  # must not raise


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
