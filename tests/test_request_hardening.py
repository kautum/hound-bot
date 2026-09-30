"""Request-boundary hardening: signature edge cases, body caps, uninstalled
workspaces, malformed-but-signed bodies, Slack Connect, command replay, docs."""

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from sqlalchemy import func, select

from app.core.security import verify_slack_signature
from app.models import InboundJob, OAuthState, ProcessedEvent, Task, Workspace

SECRET = "test-signing-secret"
ROUTE_SETTINGS = [
    "app.api.routes_events.settings.slack_signing_secret",
    "app.api.routes_commands.settings.slack_signing_secret",
    "app.api.routes_interactions.settings.slack_signing_secret",
]
GENERIC_400 = {"detail": "invalid request"}
MARKER = "ZZ-ECHO-MARKER-ZZ"
MAX_BODY = 1_048_576


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    for path in ROUTE_SETTINGS:
        monkeypatch.setattr(path, SECRET)
    monkeypatch.setattr("app.api.routes_internal.settings.cron_shared_secret", "cron-secret")
    monkeypatch.setattr("app.api.routes_commands.settings.public_base_url", "http://localhost:9")


@pytest.fixture(autouse=True)
def _loggers_enabled():
    # alembic's fileConfig (run by the migration tests) disables pre-existing
    # loggers; without this, caplog assertions depend on test order.
    for name in ("app.api.routes_events", "app.api.request_guards"):
        logging.getLogger(name).disabled = False


def _sign(body: bytes, content_type: str, secret: str = SECRET) -> dict:
    ts = str(int(time.time()))
    digest = hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": content_type,
    }


async def _post_json(client, obj_or_bytes, path="/slack/events"):
    body = obj_or_bytes if isinstance(obj_or_bytes, bytes) else json.dumps(obj_or_bytes).encode()
    return await client.post(path, content=body, headers=_sign(body, "application/json"))


async def _post_form(client, path="/slack/commands", **fields):
    body = urlencode(fields).encode()
    return await client.post(
        path, content=body, headers=_sign(body, "application/x-www-form-urlencoded")
    )


async def _post_interaction(client, payload_str: str):
    body = urlencode({"payload": payload_str}).encode()
    return await client.post(
        "/slack/interactions",
        content=body,
        headers=_sign(body, "application/x-www-form-urlencoded"),
    )


async def _install(session, team_id: str, *, uninstalled: bool = False) -> None:
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=b"x",
            key_version=1,
            installed_at=datetime.now(UTC),
            uninstalled_at=datetime.now(UTC) if uninstalled else None,
        )
    )
    await session.commit()


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


def _event(team="T1", event_id="Ev1", **overrides) -> dict:
    body = {
        "type": "event_callback",
        "event_id": event_id,
        "team_id": team,
        "event": {"type": "app_mention", "channel": "C1", "text": "hi"},
    }
    body.update(overrides)
    return body


# 1. Signature / secret comparison must never raise on attacker input


class TestSignatureEdgeCases:
    def test_huge_timestamp_returns_false(self):
        assert verify_slack_signature(b"{}", "9" * 400, "v0=abc", SECRET) is False

    def test_non_ascii_signature_returns_false(self):
        ts = str(int(time.time()))
        assert verify_slack_signature(b"{}", ts, "v0=café", SECRET) is False

    def test_still_fails_closed_when_unset(self):
        with pytest.raises(RuntimeError):
            verify_slack_signature(b"{}", "1", "v0=a", "")

    def test_valid_signature_still_accepted(self):
        body = b'{"a":1}'
        headers = _sign(body, "x")
        assert verify_slack_signature(
            body, headers["X-Slack-Request-Timestamp"], headers["X-Slack-Signature"], SECRET
        )

    @pytest.mark.parametrize("path", ["/slack/events", "/slack/commands", "/slack/interactions"])
    async def test_routes_answer_401_for_huge_timestamp(self, api_client, path):
        response = await api_client.post(
            path,
            content=b"{}",
            headers={"X-Slack-Request-Timestamp": "9" * 400, "X-Slack-Signature": "v0=a"},
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("path", ["/slack/events", "/slack/commands", "/slack/interactions"])
    async def test_routes_answer_401_for_non_ascii_signature(self, api_client, path):
        response = await api_client.post(
            path,
            content=b"{}",
            headers=[
                (b"X-Slack-Request-Timestamp", str(int(time.time())).encode()),
                (b"X-Slack-Signature", "v0=café".encode()),
            ],
        )
        assert response.status_code == 401

    async def test_cron_secret_non_ascii_is_401(self, api_client):
        response = await api_client.post(
            "/internal/tick", headers=[(b"X-Cron-Secret", "sécret".encode())]
        )
        assert response.status_code == 401


# 2. Body cap before signature work

ROUTES_AND_TYPES = [
    ("/slack/events", "application/json"),
    ("/slack/commands", "application/x-www-form-urlencoded"),
    ("/slack/interactions", "application/x-www-form-urlencoded"),
]


class TestBodyCap:
    @pytest.mark.parametrize(("path", "ctype"), ROUTES_AND_TYPES)
    async def test_oversized_buffered_body_is_413_before_signature(
        self, api_client, path, ctype
    ):
        response = await api_client.post(
            path,
            content=b"a" * (MAX_BODY + 1),
            headers={"Content-Type": ctype, "X-Slack-Signature": "v0=bad"},
        )
        assert response.status_code == 413

    @pytest.mark.parametrize(("path", "ctype"), ROUTES_AND_TYPES)
    async def test_oversized_content_length_header_is_413(self, api_client, path, ctype):
        response = await api_client.post(
            path,
            content=b"tiny",
            headers={"Content-Type": ctype, "Content-Length": str(MAX_BODY + 1)},
        )
        assert response.status_code == 413

    async def test_oversized_chunked_body_without_content_length_is_413(self, api_client):
        async def chunks():
            for _ in range(5):
                yield b"a" * 300_000

        response = await api_client.post(
            "/slack/events", content=chunks(), headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 413

    @pytest.mark.parametrize(("path", "ctype"), ROUTES_AND_TYPES)
    async def test_body_at_the_limit_is_not_413(self, api_client, path, ctype):
        response = await api_client.post(
            path,
            content=b"a" * MAX_BODY,
            headers={"Content-Type": ctype, "X-Slack-Signature": "v0=bad"},
        )
        assert response.status_code == 401


# 3. Uninstalled / missing workspace

NOT_INSTALLED_TEXT = "Hound isn't installed in this workspace. Ask a workspace admin to install it."


@pytest.mark.parametrize("uninstalled", [None, True])
class TestWorkspaceNotInstalled:
    async def _setup(self, session, uninstalled):
        if uninstalled:
            await _install(session, "T1", uninstalled=True)

    async def test_events_ignored_no_enqueue_no_dedupe_row(
        self, api_client, db_session, uninstalled, caplog
    ):
        await self._setup(db_session, uninstalled)
        with caplog.at_level(logging.INFO):
            response = await _post_json(api_client, _event())
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        assert await _count(db_session, InboundJob) == 0
        assert await _count(db_session, ProcessedEvent) == 0
        assert any(r.levelno == logging.INFO for r in caplog.records)

    @pytest.mark.parametrize(
        "text",
        ["add <@U2> Title | 2026-09-10T17:00+00:00", "list", "done abc"],
    )
    async def test_task_command_says_not_installed(
        self, api_client, db_session, uninstalled, text
    ):
        await self._setup(db_session, uninstalled)
        response = await _post_form(
            api_client, command="/task", text=text, team_id="T1", user_id="U1", channel_id="C1"
        )
        assert response.status_code == 200
        assert response.json() == {"response_type": "ephemeral", "text": NOT_INSTALLED_TEXT}
        assert await _count(db_session, Task) == 0

    async def test_link_calendar_says_not_installed(self, api_client, db_session, uninstalled):
        await self._setup(db_session, uninstalled)
        response = await _post_form(
            api_client, command="/link-calendar", text="", team_id="T1", user_id="U1"
        )
        assert response.status_code == 200
        assert response.json()["text"] == NOT_INSTALLED_TEXT
        assert await _count(db_session, OAuthState) == 0

    async def test_interactions_ignored(self, api_client, db_session, uninstalled):
        await self._setup(db_session, uninstalled)
        payload = {
            "team": {"id": "T1"},
            "user": {"id": "U1"},
            "actions": [
                {"action_id": "meet_book", "value": "00000000-0000-0000-0000-000000000001"}
            ],
        }
        response = await _post_interaction(api_client, json.dumps(payload))
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        assert await _count(db_session, InboundJob) == 0


# 4. Structurally wrong but validly signed bodies

BAD_EVENT_BODIES = {
    "not_json": b"this is not json " + MARKER.encode(),
    "json_list": b'["' + MARKER.encode() + b'"]',
    "json_string": b'"' + MARKER.encode() + b'"',
    "empty": b"",
    "invalid_utf8": b'{"a":"\xff\xfe' + MARKER.encode() + b'"}',
    "nul_raw": b'{"type":"event_callback","event_id":"a\x00b"}',
    "nul_escaped": (
        b'{"type":"event_callback","event_id":"a\\u0000b","team_id":"T1",'
        b'"event":{"type":"app_mention"}}'
    ),
    "missing_event_id": json.dumps({**_event(), "event_id": None}).encode(),
    "int_event_id": json.dumps(_event(event_id=5)).encode(),
    "long_event_id": json.dumps(_event(event_id="E" * 129)).encode(),
    "missing_team": json.dumps({**_event(), "team_id": None}).encode(),
    "int_team": json.dumps(_event(team=7)).encode(),
    "event_not_object": json.dumps(_event(event=MARKER)).encode(),
    "event_missing_type": json.dumps(_event(event={"channel": "C1"})).encode(),
    "event_int_type": json.dumps(_event(event={"type": 3})).encode(),
    "url_verification_no_challenge": b'{"type":"url_verification"}',
    "deep_nesting": b"[" * 100_000 + b"]" * 100_000,
}


class TestMalformedEvents:
    @pytest.mark.parametrize("name", list(BAD_EVENT_BODIES))
    async def test_bad_body_is_generic_400(self, api_client, db_session, name):
        await _install(db_session, "T1")
        response = await _post_json(api_client, BAD_EVENT_BODIES[name])
        assert response.status_code == 400
        assert response.json() == GENERIC_400
        assert MARKER not in response.text
        assert await _count(db_session, InboundJob) == 0

    async def test_reason_logged_at_warning_without_payload(
        self, api_client, db_session, caplog
    ):
        with caplog.at_level(logging.WARNING):
            await _post_json(api_client, b"not json " + MARKER.encode())
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings
        assert all(MARKER not in r.getMessage() for r in caplog.records)


def _click(action: dict | list | str | None = None, team=None, user=None, **extra) -> str:
    if action is None:
        action = {"action_id": "task_done", "value": "x"}
    payload = {
        "actions": action if isinstance(action, str | list) else [action],
        "team": {"id": "T1"} if team is None else team,
        "user": {"id": "U"} if user is None else user,
        **extra,
    }
    return json.dumps(payload)


BAD_INTERACTIONS = {
    "not_json": "not json " + MARKER,
    "json_list": json.dumps([MARKER]),
    "actions_not_list": _click(action=MARKER),
    "actions_not_objects": _click(action=[MARKER]),
    "team_not_object": _click(team=MARKER),
    "team_id_int": _click(team={"id": 5}),
    "user_missing": json.dumps(
        {"actions": [{"action_id": "task_done", "value": "x"}], "team": {"id": "T1"}}
    ),
    "user_id_list": _click(user={"id": ["a"]}),
    "value_null": _click(action={"action_id": "task_done", "value": None}),
    "value_int": _click(action={"action_id": "meet_book", "value": 5}),
    "action_id_missing": _click(action={"value": "x"}),
    "nul_escaped": _click(action={"action_id": "task_done", "value": "a\u0000b"}),
    "response_url_int": _click(response_url=5),
    "deep_nesting": "[" * 100_000 + "]" * 100_000,
}


class TestMalformedInteractions:
    @pytest.mark.parametrize("name", list(BAD_INTERACTIONS))
    async def test_bad_payload_is_generic_400(self, api_client, db_session, name):
        await _install(db_session, "T1")
        response = await _post_interaction(api_client, BAD_INTERACTIONS[name])
        assert response.status_code == 400
        assert response.json() == GENERIC_400
        assert MARKER not in response.text

    async def test_invalid_utf8_body_is_400(self, api_client):
        body = b"payload=%ff%fe"
        response = await api_client.post(
            "/slack/interactions",
            content=body,
            headers=_sign(body, "application/x-www-form-urlencoded"),
        )
        assert response.status_code == 400
        assert response.json() == GENERIC_400


class TestMalformedCommands:
    @pytest.mark.parametrize(
        "fields",
        [
            {"command": "/task", "text": "list", "user_id": "U1"},
            {"command": "/task", "text": "list", "team_id": "T1"},
            {"text": "list", "team_id": "T1", "user_id": "U1"},
            {"command": "/task", "text": "list", "team_id": "", "user_id": "U1"},
        ],
    )
    async def test_missing_fields_never_500(self, api_client, db_session, fields):
        await _install(db_session, "T1")
        response = await _post_form(api_client, **fields)
        assert response.status_code in (200, 400)
        if response.status_code == 400:
            assert response.json() == GENERIC_400
        else:
            assert response.json()["response_type"] == "ephemeral"

    async def test_missing_team_creates_no_rows(self, api_client, db_session):
        await _post_form(
            api_client, command="/task", text="add <@U2> T | 2026-09-10T17:00+00:00", user_id="U1"
        )
        assert await _count(db_session, Task) == 0

    @pytest.mark.parametrize("field", ["text", "channel_id", "response_url", "team_id"])
    async def test_nul_in_any_field_is_400(self, api_client, db_session, field):
        await _install(db_session, "T1")
        fields = {
            "command": "/task",
            "text": "list",
            "team_id": "T1",
            "user_id": "U1",
            "channel_id": "C1",
            "response_url": "https://x.invalid/",
        }
        fields[field] = "a\x00b" + MARKER
        response = await _post_form(api_client, **fields)
        assert response.status_code == 400
        assert response.json() == GENERIC_400
        assert MARKER not in response.text


# 5. Slack Connect


class TestSlackConnect:
    async def test_foreign_user_team_is_ignored(self, api_client, db_session):
        await _install(db_session, "T1")
        body = _event()
        body["event"]["user_team"] = "T_OTHER"
        response = await _post_json(api_client, body)
        assert response.status_code == 200
        assert response.json() == {"status": "ignored"}
        assert await _count(db_session, InboundJob) == 0

    async def test_same_user_team_is_processed(self, api_client, db_session):
        await _install(db_session, "T1")
        body = _event()
        body["event"]["user_team"] = "T1"
        response = await _post_json(api_client, body)
        assert response.json() == {"status": "ok"}
        assert await _count(db_session, InboundJob) == 1


# 6. Slash command replay


class TestCommandReplay:
    def _add(self, **extra):
        return {
            "command": "/task",
            "text": "add <@UASSIGNEE1> Replay me | 2026-09-10T17:00+00:00",
            "team_id": "T1",
            "user_id": "U1",
            "channel_id": "C1",
            **extra,
        }

    async def test_identical_replay_creates_one_task(self, api_client, db_session):
        await _install(db_session, "T1")
        first = await _post_form(api_client, **self._add(trigger_id="tr.1"))
        second = await _post_form(api_client, **self._add(trigger_id="tr.1"))
        assert "Created task" in first.json()["text"]
        assert second.status_code == 200
        assert second.json() == {
            "response_type": "ephemeral",
            "text": "Duplicate request ignored.",
        }
        assert await _count(db_session, Task) == 1
        keys = (await db_session.execute(select(ProcessedEvent.event_id))).scalars().all()
        assert "cmd:T1:tr.1" in keys

    async def test_different_trigger_ids_both_run(self, api_client, db_session):
        await _install(db_session, "T1")
        await _post_form(api_client, **self._add(trigger_id="tr.1"))
        await _post_form(api_client, **self._add(trigger_id="tr.2"))
        assert await _count(db_session, Task) == 2

    async def test_no_trigger_id_skips_dedupe(self, api_client, db_session):
        await _install(db_session, "T1")
        await _post_form(api_client, **self._add())
        await _post_form(api_client, **self._add())
        assert await _count(db_session, Task) == 2


# 8. Public docs endpoints


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_endpoints_are_disabled(api_client, path):
    assert (await api_client.get(path)).status_code == 404
