import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime

from app.models import Workspace

SIGNING_SECRET = "test-signing-secret"


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


def _signed_form_headers(body: bytes, secret: str) -> dict:
    ts = str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _form_body(**fields) -> bytes:
    from urllib.parse import urlencode

    return urlencode(fields).encode()


class TestSlackCommandsEndpoint:
    async def test_add_list_done_round_trip(self, api_client, db_session, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        await _make_workspace(db_session, "T1")

        add_body = _form_body(
            command="/task",
            text="add <@UASSIGNEE1|bob> Write the report | 2026-09-10T17:00+00:00",
            team_id="T1",
            user_id="U_CREATOR",
            channel_id="C1",
        )
        add_response = await api_client.post(
            "/slack/commands",
            content=add_body,
            headers=_signed_form_headers(add_body, SIGNING_SECRET),
        )
        assert add_response.status_code == 200
        assert "Created task" in add_response.json()["text"]

        list_body = _form_body(
            command="/task", text="list", team_id="T1", user_id="UASSIGNEE1", channel_id="C1"
        )
        list_response = await api_client.post(
            "/slack/commands",
            content=list_body,
            headers=_signed_form_headers(list_body, SIGNING_SECRET),
        )
        assert list_response.json()["response_type"] == "ephemeral"
        blocks = list_response.json()["blocks"]
        assert "Write the report" in blocks[0]["text"]["text"]
        task_id = blocks[1]["elements"][0]["value"]

        done_body = _form_body(
            command="/task",
            text=f"done {task_id}",
            team_id="T1",
            user_id="UASSIGNEE1",
            channel_id="C1",
        )
        done_response = await api_client.post(
            "/slack/commands",
            content=done_body,
            headers=_signed_form_headers(done_body, SIGNING_SECRET),
        )
        assert "Marked" in done_response.json()["text"]

    async def test_add_with_recurrence_interval(self, api_client, db_session, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        await _make_workspace(db_session, "T1")

        add_body = _form_body(
            command="/task",
            text="add <@UASSIGNEE1|bob> Weekly report | 2026-09-10T17:00+00:00 | repeat:7",
            team_id="T1",
            user_id="U_CREATOR",
            channel_id="C1",
        )
        add_response = await api_client.post(
            "/slack/commands",
            content=add_body,
            headers=_signed_form_headers(add_body, SIGNING_SECRET),
        )
        assert add_response.status_code == 200
        assert "Created task" in add_response.json()["text"]

        # Verify the task was created with recurrence_interval_days=7
        from sqlalchemy import select

        from app.models import Task

        result = await db_session.execute(select(Task).filter_by(team_id="T1"))
        task = result.scalar_one()
        assert task.recurrence_interval_days == 7

    async def test_bad_syntax_returns_usage_not_a_crash(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(
            command="/task", text="add nonsense", team_id="T1", user_id="U1", channel_id="C1"
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
        assert "Usage" in response.json()["text"]

    async def test_unknown_command_does_not_crash(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(command="/nonexistent", text="", team_id="T1", user_id="U1")
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
        assert "Unknown command" in response.json()["text"]


class TestLinkCalendarCommand:
    async def test_returns_a_link_url_carrying_only_an_opaque_state(
        self, api_client, db_session, monkeypatch
    ):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr(
            "app.api.routes_commands.settings.public_base_url", "https://example.ngrok-free.app"
        )
        body = _form_body(command="/link-calendar", text="", team_id="T1", user_id="U1")
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        text = response.json()["text"]
        assert "https://example.ngrok-free.app/google/link?state=" in text
        # The identity must never travel in the URL — only Slack's already-
        # verified signature established it, at the point the state was
        # issued server-side.
        assert "team_id=" not in text
        assert "slack_user_id=" not in text
        assert "U1" not in text

        from app.models.operational import OAuthState

        state = text.split("state=")[1].strip()
        from sqlalchemy import select

        row = (
            await db_session.execute(select(OAuthState).filter_by(state=state))
        ).scalar_one()
        assert row.team_id == "T1"
        assert row.slack_user_id == "U1"
        assert row.purpose == "google_link"


class TestMeetCommand:
    async def test_bad_syntax_returns_usage(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(command="/meet", text="nonsense", team_id="T1", user_id="U1")
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert "Usage" in response.json()["text"]

    async def test_book_performs_no_outbound_calls_and_enqueues_one_job(
        self, api_client, db_session, monkeypatch
    ):
        """S5's gate exactly as specified: /meet book must make zero
        outbound HTTP calls inside the request handler, and enqueue exactly
        one job for the worker to process instead."""
        import respx

        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_secret", "gsecret")
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key", "irrelevant-here")
        await _make_workspace(db_session, "T1")

        meeting_id = "11111111-1111-1111-1111-111111111111"

        with respx.mock(assert_all_called=False) as mock_router:
            # No routes registered at all — any outbound call raises inside
            # respx, which is exactly the assertion: zero network calls.
            body = _form_body(
                command="/meet",
                text=f"book {meeting_id}",
                team_id="T1",
                user_id="U1",
                response_url="https://hooks.slack.invalid/commands/fake",
            )
            response = await api_client.post(
                "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
            )
            assert len(mock_router.calls) == 0

        assert "Booking that meeting" in response.json()["text"]

        from sqlalchemy import select

        from app.models import InboundJob

        result = await db_session.execute(
            select(InboundJob).filter_by(team_id="T1", event_type="meet_book")
        )
        jobs = result.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].payload["meeting_id"] == meeting_id
        assert jobs[0].payload["requesting_user_id"] == "U1"

    async def test_degrades_gracefully_when_calendar_not_configured(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", None)
        body = _form_body(
            command="/meet",
            text="<@U2> 30 | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00",
            team_id="T1",
            user_id="U1",
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert "isn't configured" in response.json()["text"]

    async def test_propose_enqueues_and_acks_immediately(self, api_client, db_session, monkeypatch):
        """/meet's propose path must never call Google inline — see
        ARCHITECTURE.md's 3-second rule. This asserts the ack is instant and
        a job is queued; app.worker.handle_meet_propose does the real work,
        tested separately in tests/test_worker.py."""
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_secret", "gsecret")
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key", "irrelevant-here")
        await _make_workspace(db_session, "T1")

        body = _form_body(
            command="/meet",
            text="<@U2> 30 | 2026-09-07T09:00:00+00:00 | 2026-09-07T17:00:00+00:00",
            team_id="T1",
            user_id="U1",
            response_url="https://hooks.slack.invalid/commands/fake",
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )

        assert "Looking for a mutual free slot" in response.json()["text"]

        from sqlalchemy import select

        from app.models import InboundJob

        result = await db_session.execute(
            select(InboundJob).filter_by(team_id="T1", event_type="meet_propose")
        )
        jobs = result.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].payload["organiser_id"] == "U1"
        assert jobs[0].payload["participant_slack_ids"] == ["U2"]
        assert jobs[0].payload["response_url"] == "https://hooks.slack.invalid/commands/fake"

    async def test_propose_then_book_end_to_end(self, api_client, db_session, monkeypatch):
        """Full round trip: enqueue via the endpoint, run the worker handler
        that would normally process it, then book via the endpoint again."""
        import httpx
        import respx
        from cryptography.fernet import Fernet

        from app.calendar.google_calendar import (
            GOOGLE_EVENTS_URL,
            GOOGLE_FREEBUSY_URL,
            GOOGLE_TOKEN_URL,
        )
        from app.core.security import TokenCipher
        from app.models import User
        from app.worker import handle_meet_propose

        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_secret", "gsecret")
        monkeypatch.setattr("app.worker.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.worker.settings.google_client_secret", "gsecret")
        key = Fernet.generate_key().decode()
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key", key)
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key_version", 1)

        await _make_workspace(db_session, "T1")
        cipher = TokenCipher(keys={1: key}, current_version=1)
        token_enc, version = cipher.encrypt("tok-alice")
        db_session.add(
            User(
                slack_user_id="U1",
                team_id="T1",
                tz="UTC",
                google_refresh_token_enc=token_enc,
                key_version=version,
                google_email="a@x.com",
            )
        )
        await db_session.commit()

        response_url = "https://hooks.slack.invalid/commands/fake"

        with respx.mock:
            respx.post(GOOGLE_TOKEN_URL).mock(
                return_value=httpx.Response(200, json={"access_token": "ya29.fake"})
            )
            respx.post(GOOGLE_FREEBUSY_URL).mock(
                return_value=httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})
            )
            response_url_route = respx.post(response_url).mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            propose_body = _form_body(
                command="/meet",
                text="<@U1> 30 | 2026-09-07T09:00:00+00:00 | 2026-09-07T17:00:00+00:00",
                team_id="T1",
                user_id="U1",
                response_url=response_url,
            )
            propose_response = await api_client.post(
                "/slack/commands",
                content=propose_body,
                headers=_signed_form_headers(propose_body, SIGNING_SECRET),
            )
            assert "Looking for a mutual free slot" in propose_response.json()["text"]

            from sqlalchemy import select as sa_select

            from app.models import InboundJob

            job_result = await db_session.execute(
                sa_select(InboundJob).filter_by(team_id="T1", event_type="meet_propose")
            )
            job = job_result.scalars().one()

            # This is what the worker loop would do automatically in
            # production — invoked directly here since this test doesn't
            # run the whole polling loop.
            await handle_meet_propose(db_session, job, cipher)

            follow_up = json.loads(response_url_route.calls.last.request.content)
            follow_up_text = follow_up["text"]
            assert "Earliest mutual slot" in follow_up_text
            assert "2026-09-07T09:00:00+00:00" in follow_up_text
            # Extract meeting_id from the blocks (section block contains the meeting ID)
            blocks = follow_up.get("blocks", [])
            assert len(blocks) == 2
            assert blocks[0]["type"] == "section"
            meeting_id = blocks[0]["text"]["text"].split("Meeting ID: `")[1].split("`")[0]

            respx.post(GOOGLE_EVENTS_URL).mock(
                return_value=httpx.Response(200, json={"id": "evt_end_to_end"})
            )

            book_body = _form_body(
                command="/meet",
                text=f"book {meeting_id}",
                team_id="T1",
                user_id="U1",
                response_url=response_url,
            )
            book_response = await api_client.post(
                "/slack/commands",
                content=book_body,
                headers=_signed_form_headers(book_body, SIGNING_SECRET),
            )

            # S5: booking must ack-and-enqueue too, exactly like propose —
            # the two Google calls (refresh, events.insert) happen in the
            # worker, never inline in the command handler.
            assert "Booking that meeting" in book_response.json()["text"]

            from app.worker import handle_meet_book

            book_job_result = await db_session.execute(
                sa_select(InboundJob).filter_by(team_id="T1", event_type="meet_book")
            )
            book_job = book_job_result.scalars().one()
            await handle_meet_book(db_session, book_job, cipher)

            booked_text = json.loads(response_url_route.calls.last.request.content)["text"]

        assert "Booked for" in booked_text

        import uuid

        from sqlalchemy import select

        from app.models.meeting import STATUS_BOOKED, Meeting

        result = await db_session.execute(select(Meeting).filter_by(id=uuid.UUID(meeting_id)))
        meeting = result.scalar_one()
        assert meeting.status == STATUS_BOOKED
        assert meeting.google_event_id == "evt_end_to_end"


class TestTaskReassignCommand:
    async def test_reassign_task_command(self, api_client, db_session, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        await _make_workspace(db_session, "T1")

        # First create a task
        add_body = _form_body(
            command="/task",
            text="add <@UASSIGNEE1|bob> Write the report | 2026-09-10T17:00+00:00",
            team_id="T1",
            user_id="U_CREATOR",
            channel_id="C1",
        )
        add_response = await api_client.post(
            "/slack/commands",
            content=add_body,
            headers=_signed_form_headers(add_body, SIGNING_SECRET),
        )
        assert add_response.status_code == 200

        # Get the task ID from the list command
        list_body = _form_body(
            command="/task", text="list", team_id="T1", user_id="UASSIGNEE1", channel_id="C1"
        )
        list_response = await api_client.post(
            "/slack/commands",
            content=list_body,
            headers=_signed_form_headers(list_body, SIGNING_SECRET),
        )
        task_id = list_response.json()["blocks"][1]["elements"][0]["value"]

        # Reassign as the creator
        reassign_body = _form_body(
            command="/task",
            text=f"reassign {task_id} <@UASSIGNEE2|alice>",
            team_id="T1",
            user_id="U_CREATOR",
            channel_id="C1",
        )
        reassign_response = await api_client.post(
            "/slack/commands",
            content=reassign_body,
            headers=_signed_form_headers(reassign_body, SIGNING_SECRET),
        )
        assert reassign_response.status_code == 200
        assert "Reassigned" in reassign_response.json()["text"]

    async def test_reassign_invalid_uuid(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(
            command="/task",
            text="reassign not-a-uuid <@U2>",
            team_id="T1",
            user_id="U1",
            channel_id="C1",
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
        assert "isn't a valid task ID" in response.json()["text"]

    async def test_list_returns_block_kit_with_mark_done_buttons(
        self, api_client, db_session, monkeypatch
    ):
        """Test that /task list returns Block Kit format with Mark done buttons."""
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        await _make_workspace(db_session, "T1")

        add_body = _form_body(
            command="/task",
            text="add <@UASSIGNEE1|bob> Write the report | 2026-09-10T17:00+00:00",
            team_id="T1",
            user_id="U_CREATOR",
            channel_id="C1",
        )
        add_response = await api_client.post(
            "/slack/commands",
            content=add_body,
            headers=_signed_form_headers(add_body, SIGNING_SECRET),
        )
        assert add_response.status_code == 200

        list_body = _form_body(
            command="/task", text="list", team_id="T1", user_id="UASSIGNEE1", channel_id="C1"
        )
        list_response = await api_client.post(
            "/slack/commands",
            content=list_body,
            headers=_signed_form_headers(list_body, SIGNING_SECRET),
        )
        assert list_response.status_code == 200
        assert list_response.json()["response_type"] == "ephemeral"
        blocks = list_response.json()["blocks"]

        # Should have 2 blocks per task: section + actions
        assert len(blocks) == 2

        # First block is a section with the task title
        assert blocks[0]["type"] == "section"
        assert "Write the report" in blocks[0]["text"]["text"]

        # Second block is an actions block with a button
        assert blocks[1]["type"] == "actions"
        assert len(blocks[1]["elements"]) == 1
        button = blocks[1]["elements"][0]
        assert button["type"] == "button"
        assert button["text"]["text"] == "Mark done"
        assert button["action_id"] == "task_done"

        # The button value should be a valid UUID (the task ID)
        uuid.UUID(button["value"])  # Will raise if invalid


class TestMeetCancelCommand:
    async def test_cancel_command_enqueues_job(self, api_client, db_session, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_id", "gcid")
        monkeypatch.setattr("app.api.routes_commands.settings.google_client_secret", "gsecret")
        monkeypatch.setattr("app.api.routes_commands.settings.encryption_key", "irrelevant-here")
        await _make_workspace(db_session, "T1")

        meeting_id = "11111111-1111-1111-1111-111111111111"

        body = _form_body(
            command="/meet",
            text=f"cancel {meeting_id}",
            team_id="T1",
            user_id="U1",
            response_url="https://hooks.slack.invalid/commands/fake",
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )

        assert "Cancelling that meeting" in response.json()["text"]

        from sqlalchemy import select

        from app.models import InboundJob

        result = await db_session.execute(
            select(InboundJob).filter_by(team_id="T1", event_type="meet_cancel")
        )
        jobs = result.scalars().all()
        assert len(jobs) == 1
        assert jobs[0].payload["meeting_id"] == meeting_id
        assert jobs[0].payload["requesting_user_id"] == "U1"

    async def test_cancel_invalid_uuid(self, api_client, monkeypatch):
        monkeypatch.setattr("app.api.routes_commands.settings.slack_signing_secret", SIGNING_SECRET)
        body = _form_body(
            command="/meet",
            text="cancel not-a-uuid",
            team_id="T1",
            user_id="U1",
        )
        response = await api_client.post(
            "/slack/commands", content=body, headers=_signed_form_headers(body, SIGNING_SECRET)
        )
        assert response.status_code == 200
        assert "isn't a valid meeting ID" in response.json()["text"]
