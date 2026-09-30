"""Two output-side protections for text Slack renders:

* user-controlled task titles must not become live mrkdwn (`<!channel>`,
  `<@U...>`, `<https://evil|click me>`), and
* task lists must stay inside Slack's block limits no matter how many tasks exist.
"""

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from app.agent.loop import GROQ_CHAT_URL, run_agent_turn
from app.agent.tools import AgentContext
from app.core.security import TokenCipher
from app.models import InboundJob, Task, Workspace
from app.scheduler import process_due_reminders
from app.services.task_service import create_task
from app.ui.blocks import (
    MAX_TASKS_SHOWN,
    build_app_home_view,
    build_task_list_blocks,
    escape_mrkdwn,
)
from app.worker import handle_app_home_opened

SIGNING_SECRET = "test-signing-secret"
SLACK_MESSAGE_BLOCK_LIMIT = 50
APP_HOME_BLOCK_LIMIT = 100

HOSTILE_TITLES = [
    "<!channel>",
    "<!here>",
    "<@UEVIL>",
    "<https://evil.example|click me>",
    "a & b",
]
# Substrings that would be live in Slack if they survived into rendered text.
LIVE_FRAGMENTS = ["<!channel>", "<!here>", "<@UEVIL>", "<https://evil.example|click me>"]


def _assert_no_live_mrkdwn(rendered: str) -> None:
    for fragment in LIVE_FRAGMENTS:
        assert fragment not in rendered, f"{fragment!r} is live in {rendered!r}"


def _task(title: str, due_offset_minutes: int = 0) -> Task:
    return Task(
        id=uuid.uuid4(),
        team_id="T1",
        creator_slack_id="U1",
        assignee_slack_id="U1",
        title=title,
        due_at_utc=datetime(2030, 1, 1, tzinfo=UTC) + timedelta(minutes=due_offset_minutes),
        status="open",
        channel_id="C1",
    )


async def _make_workspace(session, team_id: str, cipher: TokenCipher | None = None) -> None:
    token_enc, version = cipher.encrypt("xoxb-fake") if cipher else (b"irrelevant", 1)
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


def _signed_headers(body: bytes) -> dict:
    ts = str(int(time.time()))
    digest = hmac.new(
        SIGNING_SECRET.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _slash(api_client, monkeypatch, **fields) -> dict:
    monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
    body = urlencode({"command": "/task", "team_id": "T1", "channel_id": "C1", **fields}).encode()
    response = await api_client.post(
        "/slack/commands", content=body, headers=_signed_headers(body)
    )
    assert response.status_code == 200
    return response.json()


class TestEscapeMrkdwn:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("<!channel>", "&lt;!channel&gt;"),
            ("<@UEVIL>", "&lt;@UEVIL&gt;"),
            ("<https://evil.example|click me>", "&lt;https://evil.example|click me&gt;"),
            ("a & b", "a &amp; b"),
            ("plain", "plain"),
            ("", ""),
        ],
    )
    def test_escapes_exactly_the_three_control_characters(self, raw, expected):
        assert escape_mrkdwn(raw) == expected

    def test_ampersand_is_escaped_first_so_output_is_not_double_escaped(self):
        # "<" -> "&lt;" must not then have its "&" turned into "&amp;lt;".
        assert escape_mrkdwn("<") == "&lt;"
        # Already-escaped input is data, escaped once per call (caller applies once).
        assert escape_mrkdwn("&lt;") == "&amp;lt;"


class TestBlocksEscapeTitles:
    @pytest.mark.parametrize("title", HOSTILE_TITLES)
    def test_task_list_and_app_home_escape_the_title(self, title):
        for blocks in (
            build_task_list_blocks([_task(title)]),
            build_app_home_view([_task(title)])["blocks"],
        ):
            text = blocks[0]["text"]["text"]
            _assert_no_live_mrkdwn(text)
            assert f"*{escape_mrkdwn(title)}*" in text

    def test_meeting_action_block_escapes_the_id(self):
        from app.ui.blocks import build_meeting_action_blocks

        blocks = build_meeting_action_blocks("<!channel>", "meet_book", "Book")
        assert "<!channel>" not in blocks[0]["text"]["text"]


class TestReminderTextEscapesTitles:
    @pytest.mark.parametrize("title", HOSTILE_TITLES)
    async def test_reminder_dm_text_and_blocks_are_escaped(self, db_session, monkeypatch, title):
        cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)
        await _make_workspace(db_session, "T1", cipher)
        await create_task(
            db_session,
            team_id="T1",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title=title,
            due_at_utc=datetime.now(UTC) - timedelta(hours=1),
            channel_id="C1",
        )
        await db_session.commit()
        posted: list[tuple[str, list[dict]]] = []

        class Client:
            async def conversations_open(self, users):
                return {"channel": {"id": f"D-{users}"}}

            async def chat_postMessage(self, channel, text, blocks=None):
                posted.append((text, blocks or []))

        monkeypatch.setattr(
            "app.scheduler.build_client_for_workspace", lambda workspace, cipher: Client()
        )

        assert await process_due_reminders(db_session, cipher) == 1

        text, blocks = posted[0]
        _assert_no_live_mrkdwn(text)
        _assert_no_live_mrkdwn(blocks[0]["text"]["text"])
        assert escape_mrkdwn(title) in text


class TestCommandEchoesEscapeTitles:
    @pytest.mark.parametrize("title", HOSTILE_TITLES)
    async def test_add_done_and_reassign_echoes_are_escaped(
        self, api_client, db_session, monkeypatch, title
    ):
        await _make_workspace(db_session, "T1")

        added = await _slash(
            api_client,
            monkeypatch,
            user_id="U_CREATOR",
            text=f"add <@UASSIGNEE> {title} | 2030-01-01T00:00+00:00",
        )
        _assert_no_live_mrkdwn(added["text"])
        assert escape_mrkdwn(title) in added["text"]

        listed = await _slash(api_client, monkeypatch, user_id="UASSIGNEE", text="list")
        task_id = listed["blocks"][1]["elements"][0]["value"]

        reassigned = await _slash(
            api_client,
            monkeypatch,
            user_id="UASSIGNEE",
            text=f"reassign {task_id} <@UNEWONE>",
        )
        _assert_no_live_mrkdwn(reassigned["text"])
        assert "<@UNEWONE>" in reassigned["text"]  # the intentional mention stays live

        done = await _slash(api_client, monkeypatch, user_id="UNEWONE", text=f"done {task_id}")
        _assert_no_live_mrkdwn(done["text"])
        assert escape_mrkdwn(title) in done["text"]


class TestAgentReplyNeutralisesBroadcasts:
    @respx.mock
    async def test_direct_reply_cannot_broadcast(self, db_session):
        respx.post(GROQ_CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "hi <!channel> and <!here>"}}
                    ]
                },
            )
        )
        ctx = AgentContext(session=db_session, team_id="T1", slack_user_id="U1")
        async with httpx.AsyncClient() as client:
            reply = await run_agent_turn(ctx, client, "k", user_message="hello")
        assert "<!" not in reply
        assert "&lt;!channel>" in reply and "&lt;!here>" in reply

    @respx.mock
    async def test_reply_after_tool_call_cannot_broadcast(self, db_session):
        tool_call = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "list_tasks", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        }
        final = {"choices": [{"message": {"role": "assistant", "content": "<!channel> done"}}]}
        route = respx.post(GROQ_CHAT_URL)
        route.side_effect = [httpx.Response(200, json=tool_call), httpx.Response(200, json=final)]
        ctx = AgentContext(session=db_session, team_id="T1", slack_user_id="U1")
        async with httpx.AsyncClient() as client:
            reply = await run_agent_turn(ctx, client, "k", user_message="hello")
        assert "<!" not in reply
        assert reply == "&lt;!channel> done"


TASK_COUNTS = [0, MAX_TASKS_SHOWN, MAX_TASKS_SHOWN + 1, 500]


def _expected_overflow(count: int) -> int:
    return max(0, count - MAX_TASKS_SHOWN)


def _split_overflow(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    task_blocks = [b for b in blocks if b["type"] in ("section", "actions")]
    context_blocks = [b for b in blocks if b["type"] == "context"]
    return task_blocks, context_blocks


async def _insert_tasks(session, count: int) -> None:
    base = datetime(2030, 1, 1, tzinfo=UTC)
    # Inserted latest-due first, so "earliest due first" can't come from insertion order.
    session.add_all(
        Task(
            team_id="T1",
            creator_slack_id="U1",
            assignee_slack_id="U1",
            title=f"task {i:04d}",
            due_at_utc=base + timedelta(minutes=i),
            channel_id="C1",
        )
        for i in reversed(range(count))
    )
    await session.commit()


class TestBlockBuildersCap:
    @pytest.mark.parametrize("count", TASK_COUNTS)
    @pytest.mark.parametrize("view", ["list", "home"])
    def test_builder_renders_at_most_the_cap_plus_an_exact_overflow_note(self, count, view):
        tasks = [_task(f"t{i}", i) for i in range(count)]
        if view == "list":
            blocks = build_task_list_blocks(tasks)
        else:
            blocks = build_app_home_view(tasks)["blocks"]

        task_blocks, context_blocks = _split_overflow(blocks)
        if count == 0 and view == "home":
            assert blocks[0]["text"]["text"] == "No open tasks assigned to you."
            return
        assert len(task_blocks) == 2 * min(count, MAX_TASKS_SHOWN)
        overflow = _expected_overflow(count)
        if overflow:
            assert len(context_blocks) == 1
            assert context_blocks[0]["elements"][0]["text"] == f"…and {overflow} more open tasks"
        else:
            assert context_blocks == []

    def test_caps_respect_the_slack_block_limits(self):
        assert 2 * MAX_TASKS_SHOWN + 1 <= SLACK_MESSAGE_BLOCK_LIMIT
        assert 2 * MAX_TASKS_SHOWN + 1 <= APP_HOME_BLOCK_LIMIT

    def test_overflow_uses_the_true_total_when_given(self):
        tasks = [_task(f"t{i}", i) for i in range(MAX_TASKS_SHOWN)]
        blocks = build_task_list_blocks(tasks, total_open=7352)
        assert blocks[-1]["elements"][0]["text"] == f"…and {7352 - MAX_TASKS_SHOWN} more open tasks"


class TestEndToEndCap:
    @pytest.mark.parametrize("count", TASK_COUNTS)
    async def test_slash_list_is_bounded_ordered_and_counts_overflow(
        self, api_client, db_session, monkeypatch, count
    ):
        await _make_workspace(db_session, "T1")
        await _insert_tasks(db_session, count)

        result = await _slash(api_client, monkeypatch, user_id="U1", text="list")

        if count == 0:
            assert result["text"] == "No open tasks assigned to you."
            return
        blocks = result["blocks"]
        assert len(blocks) <= SLACK_MESSAGE_BLOCK_LIMIT
        task_blocks, context_blocks = _split_overflow(blocks)
        sections = [b for b in task_blocks if b["type"] == "section"]
        assert len(sections) == min(count, MAX_TASKS_SHOWN)
        # Earliest due first: the shown tasks are exactly the first N by due date.
        shown = [s["text"]["text"] for s in sections]
        assert all(f"task {i:04d}" in shown[i] for i in range(len(shown)))
        overflow = _expected_overflow(count)
        if overflow:
            assert context_blocks[0]["elements"][0]["text"] == f"…and {overflow} more open tasks"
        else:
            assert context_blocks == []

    @pytest.mark.parametrize("count", TASK_COUNTS)
    async def test_app_home_is_bounded_ordered_and_counts_overflow(
        self, db_session, monkeypatch, count
    ):
        cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)
        await _make_workspace(db_session, "T1", cipher)
        await _insert_tasks(db_session, count)
        published: list[dict] = []

        class Client:
            async def views_publish(self, user_id, view):
                published.append(view)

        job = InboundJob(
            team_id="T1",
            event_type="app_home_opened",
            payload={"user": "U1"},
            status="pending",
            created_at=datetime.now(UTC),
        )
        monkeypatch.setattr(
            "app.worker.build_client_for_workspace", lambda workspace, cipher: Client()
        )
        await handle_app_home_opened(db_session, job, cipher)

        blocks = published[0]["blocks"]
        assert len(blocks) <= APP_HOME_BLOCK_LIMIT
        if count == 0:
            assert blocks[0]["text"]["text"] == "No open tasks assigned to you."
            return
        task_blocks, context_blocks = _split_overflow(blocks)
        sections = [b for b in task_blocks if b["type"] == "section"]
        assert len(sections) == min(count, MAX_TASKS_SHOWN)
        assert f"task {0:04d}" in sections[0]["text"]["text"]
        overflow = _expected_overflow(count)
        if overflow:
            assert context_blocks[0]["elements"][0]["text"] == f"…and {overflow} more open tasks"
        else:
            assert context_blocks == []

