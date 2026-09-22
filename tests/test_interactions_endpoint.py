import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import respx
from sqlalchemy import select

from app.models import InboundJob, Meeting, Reminder, Workspace
from app.models.meeting import STATUS_PROPOSED

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


def _build_interaction_payload(
    *,
    action_id: str = "task_done",
    value: str,
    team_id: str = "T1",
    user_id: str = "U1",
    response_url: str = "https://hooks.slack.invalid/commands/fake",
) -> str:
    payload = {
        "type": "block_actions",
        "team": {"id": team_id},
        "user": {"id": user_id},
        "actions": [{"action_id": action_id, "value": value}],
        "response_url": response_url,
    }
    return json.dumps(payload)


class TestSlackInteractionsEndpoint:
    async def test_invalid_signature_returns_401(
        self, api_client, db_session, monkeypatch
    ):
        """Tampered/invalid signature returns 401 and does NOT touch the database."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        from app.services.task_service import create_task

        due = datetime.now(UTC) + timedelta(days=1)
        task = await create_task(
            db_session,
            team_id="T1",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Test task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        initial_status = task.status

        payload_json = _build_interaction_payload(value=str(uuid.uuid4()))
        body = _form_body(payload=payload_json)
        response = await api_client.post(
            "/slack/interactions",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=badsignature",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid Slack signature"

        # Verify task status is unchanged (database was not touched)
        await db_session.refresh(task)
        assert task.status == initial_status

    async def test_valid_task_done_click_marks_task_done(
        self, api_client, db_session, monkeypatch
    ):
        """A correctly-signed task_done click from the task's actual assignee marks it done."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        from app.services.task_service import create_task

        due = datetime.now(UTC) + timedelta(days=1)
        task = await create_task(
            db_session,
            team_id="T1",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Test task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        # Verify reminders were created
        reminders_before = await db_session.execute(select(Reminder).filter_by(task_id=task.id))
        assert len(reminders_before.scalars().all()) > 0

        payload_json = _build_interaction_payload(
            value=str(task.id), team_id="T1", user_id="U_ASSIGNEE"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify task was marked done
        await db_session.refresh(task)
        assert task.status == "done"

        # Verify reminders were cancelled (side effect from mark_task_done)
        reminders_after = await db_session.execute(
            select(Reminder).filter_by(task_id=task.id, sent_at=None)
        )
        assert reminders_after.scalars().all() == []

        # Verify confirmation message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Marked" in sent_json["text"]
        assert "Test task" in sent_json["text"]

    async def test_third_party_click_does_not_mark_done(
        self, api_client, db_session, monkeypatch
    ):
        """A correctly-signed click from a random third party does NOT mark it done."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        from app.services.task_service import create_task

        due = datetime.now(UTC) + timedelta(days=1)
        task = await create_task(
            db_session,
            team_id="T1",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Test task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        initial_status = task.status

        payload_json = _build_interaction_payload(
            value=str(task.id), team_id="T1", user_id="U_RANDOM"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify task status was NOT changed
        await db_session.refresh(task)
        assert task.status == initial_status

        # Verify authorization error message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Only the assignee or the creator" in sent_json["text"]

    async def test_unknown_action_id_does_not_call_mark_task_done(
        self, api_client, db_session, monkeypatch
    ):
        """An unrecognized action_id does not call mark_task_done at all."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        from app.services.task_service import create_task

        due = datetime.now(UTC) + timedelta(days=1)
        task = await create_task(
            db_session,
            team_id="T1",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Test task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        initial_status = task.status

        payload_json = _build_interaction_payload(
            action_id="unknown_action", value=str(task.id), team_id="T1", user_id="U_ASSIGNEE"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify task status was NOT changed
        await db_session.refresh(task)
        assert task.status == initial_status

        # Verify unknown action message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Unknown action" in sent_json["text"]

    async def test_invalid_uuid_value_returns_error(
        self, api_client, db_session, monkeypatch
    ):
        """Invalid UUID in the button value returns a clear error."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        payload_json = _build_interaction_payload(
            value="not-a-uuid", team_id="T1", user_id="U1"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify error message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Invalid task ID" in sent_json["text"]

    async def test_creator_can_mark_task_done(
        self, api_client, db_session, monkeypatch
    ):
        """The creator (not just the assignee) can mark a task done via button."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        from app.services.task_service import create_task

        due = datetime.now(UTC) + timedelta(days=1)
        task = await create_task(
            db_session,
            team_id="T1",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Test task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        payload_json = _build_interaction_payload(
            value=str(task.id), team_id="T1", user_id="U_CREATOR"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify task was marked done
        await db_session.refresh(task)
        assert task.status == "done"

    async def test_meet_book_enqueues_job_with_correct_payload(
        self, api_client, db_session, monkeypatch
    ):
        """A meet_book button click enqueues a job with the correct event_type and payload."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        meeting_id = uuid.uuid4()
        db_session.add(
            Meeting(
                id=meeting_id,
                team_id="T1",
                organiser_slack_id="U_ORG",
                duration_min=30,
                status=STATUS_PROPOSED,
                proposed_start_utc=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        payload_json = _build_interaction_payload(
            action_id="meet_book", value=str(meeting_id), team_id="T1", user_id="U_ORG"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify job was enqueued
        result = await db_session.execute(
            select(InboundJob).filter_by(event_type="meet_book", team_id="T1")
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.payload["meeting_id"] == str(meeting_id)
        assert job.payload["requesting_user_id"] == "U_ORG"
        assert job.payload["response_url"] == "https://hooks.slack.invalid/commands/fake"

        # Verify confirmation message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Booking that meeting" in sent_json["text"]

    async def test_meet_book_third_party_allowed(
        self, api_client, db_session, monkeypatch
    ):
        """A third party (not organiser) is allowed to click Book — the worker
        will enforce authorization later via meeting_service.confirm_meeting."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        meeting_id = uuid.uuid4()
        db_session.add(
            Meeting(
                id=meeting_id,
                team_id="T1",
                organiser_slack_id="U_ORG",
                duration_min=30,
                status=STATUS_PROPOSED,
                proposed_start_utc=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        payload_json = _build_interaction_payload(
            action_id="meet_book", value=str(meeting_id), team_id="T1", user_id="U_RANDOM"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify job was enqueued (authorization is enforced in the worker, not here)
        result = await db_session.execute(
            select(InboundJob).filter_by(event_type="meet_book", team_id="T1")
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.payload["requesting_user_id"] == "U_RANDOM"

    async def test_meet_cancel_enqueues_job_with_correct_payload(
        self, api_client, db_session, monkeypatch
    ):
        """A meet_cancel button click from the organiser enqueues a job with the correct payload."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        meeting_id = uuid.uuid4()
        db_session.add(
            Meeting(
                id=meeting_id,
                team_id="T1",
                organiser_slack_id="U_ORG",
                duration_min=30,
                status=STATUS_PROPOSED,
                proposed_start_utc=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        payload_json = _build_interaction_payload(
            action_id="meet_cancel", value=str(meeting_id), team_id="T1", user_id="U_ORG"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify job was enqueued
        result = await db_session.execute(
            select(InboundJob).filter_by(event_type="meet_cancel", team_id="T1")
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.payload["meeting_id"] == str(meeting_id)
        assert job.payload["requesting_user_id"] == "U_ORG"
        assert job.payload["response_url"] == "https://hooks.slack.invalid/commands/fake"

        # Verify confirmation message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Cancelling that meeting" in sent_json["text"]

    async def test_meet_cancel_third_party_rejected(
        self, api_client, db_session, monkeypatch
    ):
        """A third party (not organiser) clicking Cancel is rejected immediately
        and does NOT enqueue a job."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        meeting_id = uuid.uuid4()
        db_session.add(
            Meeting(
                id=meeting_id,
                team_id="T1",
                organiser_slack_id="U_ORG",
                duration_min=30,
                status=STATUS_PROPOSED,
                proposed_start_utc=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db_session.commit()

        payload_json = _build_interaction_payload(
            action_id="meet_cancel", value=str(meeting_id), team_id="T1", user_id="U_RANDOM"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify NO job was enqueued
        result = await db_session.execute(
            select(InboundJob).filter_by(event_type="meet_cancel", team_id="T1")
        )
        job = result.scalar_one_or_none()
        assert job is None

        # Verify rejection message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Only the meeting organiser can cancel it" in sent_json["text"]

    async def test_meet_book_invalid_uuid_returns_error(
        self, api_client, db_session, monkeypatch
    ):
        """Invalid UUID in meet_book button value returns a clear error."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        payload_json = _build_interaction_payload(
            action_id="meet_book", value="not-a-uuid", team_id="T1", user_id="U1"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify error message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Invalid meeting ID" in sent_json["text"]

    async def test_meet_cancel_invalid_uuid_returns_error(
        self, api_client, db_session, monkeypatch
    ):
        """Invalid UUID in meet_cancel button value returns a clear error."""
        monkeypatch.setattr(
            "app.api.routes_interactions.settings.slack_signing_secret", SIGNING_SECRET
        )
        await _make_workspace(db_session, "T1")

        payload_json = _build_interaction_payload(
            action_id="meet_cancel", value="not-a-uuid", team_id="T1", user_id="U1"
        )
        body = _form_body(payload=payload_json)

        with respx.mock:
            response_url_route = respx.post("https://hooks.slack.invalid/commands/fake").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )

            response = await api_client.post(
                "/slack/interactions",
                content=body,
                headers=_signed_form_headers(body, SIGNING_SECRET),
            )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        # Verify error message was sent
        assert response_url_route.calls.last is not None
        sent_json = json.loads(response_url_route.calls.last.request.content)
        assert "Invalid meeting ID" in sent_json["text"]
