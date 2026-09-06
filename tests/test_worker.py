import json
from datetime import UTC, datetime

import httpx
import respx
from cryptography.fernet import Fernet

from app.agent.loop import GROQ_CHAT_URL
from app.core.security import TokenCipher
from app.models import InboundJob, Workspace
from app.worker import _strip_bot_mention, handle_app_mention


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
