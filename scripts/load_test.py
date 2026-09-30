#!/usr/bin/env python3
"""Load test for the Slack endpoints, verifying the 3-second ack budget under concurrency.

Runs the app in-process (httpx ASGITransport) against the throwaway database swa_devin and
exercises the REAL ack paths. A request from a team with no installed workspace is rejected
early, so this script first seeds one installed workspace for its own test team, then fires:

  * N signed `/task add` commands  (each inserts a task plus its reminders), then
  * N signed `app_mention` events  (each has a distinct event_id and enqueues an inbound job),

a 1:1 ratio, each phase with N requests in flight at once. Afterwards it checks the database:
every request must return 200, tasks created must equal successful /task add responses, and
jobs created must equal successful events. The rows it wrote are removed at the end.

Exits 0 only if all checks pass and p95 < 3000ms for both endpoints, 1 otherwise.

Keep --concurrent at or below 100. Above about 100 concurrent connections the httpx client
itself becomes the bottleneck and inflates the measured latency (measured), so the numbers
stop describing the server. The default stays 50.

Hard guard: refuses to run against any database other than swa_devin. The schema is created
with Base.metadata.create_all (fine for this throwaway DB; alembic is not involved).
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

# Add parent directory to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

ALLOWED_DB_NAME = "swa_devin"
# Set before the app is imported: environment variables win over .env in Settings.
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://kpkautum@localhost:5432/{ALLOWED_DB_NAME}"

SIGNING_SECRET = "test-signing-secret"
os.environ["SLACK_SIGNING_SECRET"] = SIGNING_SECRET

TEST_TEAM_ID = "TLOADTEST1"
TEST_USER_ID = "ULOADTEST1"
TEST_CHANNEL_ID = "CLOADTEST1"
THRESHOLD_MS = 3000


def _assert_safe_database(database_url: str) -> None:
    """Refuse to run (and to delete rows) anywhere but swa_devin."""
    match = re.search(r"/([^/?]+)(?:\?|$)", database_url)
    db_name = match.group(1) if match else None
    if db_name != ALLOWED_DB_NAME:
        raise RuntimeError(
            f"Refusing to load-test against database {db_name!r}; only {ALLOWED_DB_NAME!r}."
        )


def _signed_headers(body, secret, content_type, timestamp=None):
    """Mirrors Slack's documented HMAC signing scheme."""
    ts = timestamp or str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": content_type,
    }


async def _timed_post(client, path, body, content_type):
    """POST a signed body; return (latency_ms, status_code). A transport error is status 0."""
    headers = _signed_headers(body, SIGNING_SECRET, content_type)
    start = time.perf_counter()
    try:
        response = await client.post(path, content=body, headers=headers)
        status = response.status_code
    except httpx.HTTPError:
        status = 0
    return (time.perf_counter() - start) * 1000, status


async def _fire_events_request(client, run_id, index):
    """One signed app_mention event with a distinct event_id (enqueues an inbound job)."""
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": f"EvLoad_{run_id}_{index}",
            "team_id": TEST_TEAM_ID,
            "event": {"type": "app_mention", "channel": TEST_CHANNEL_ID, "text": "hi"},
        }
    ).encode()
    return await _timed_post(client, "/slack/events", body, "application/json")


async def _fire_commands_request(client, run_id, index):
    """One signed `/task add` command with a distinct trigger_id (inserts a task)."""
    due = (datetime.now(UTC) + timedelta(days=7)).replace(microsecond=0).isoformat()
    form_data = {
        "command": "/task",
        "text": f"add <@{TEST_USER_ID}> load test task {index} | {due}",
        "team_id": TEST_TEAM_ID,
        "user_id": TEST_USER_ID,
        "channel_id": TEST_CHANNEL_ID,
        "trigger_id": f"TrLoad_{run_id}_{index}",
    }
    body = urlencode(form_data).encode("utf-8")
    return await _timed_post(
        client, "/slack/commands", body, "application/x-www-form-urlencoded"
    )


def _percentiles(data: list) -> dict:
    """Calculate p50, p95, p99 percentiles and max."""
    if not data:
        return {"p50": 0, "p95": 0, "p99": 0, "max": 0}
    sorted_data = sorted(data)
    n = len(sorted_data)
    return {
        "p50": sorted_data[int(n * 0.5)],
        "p95": sorted_data[int(n * 0.95)],
        "p99": sorted_data[int(n * 0.99)],
        "max": max(data),
    }


async def _prepare_database(engine) -> bool:
    """Create the schema, clear leftovers from a crashed run, and seed one installed
    workspace for the test team if absent. Returns True if this call created it."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import Base, Workspace

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _delete_test_rows(engine, include_workspace=False)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        existing = await session.scalar(
            select(Workspace).where(Workspace.team_id == TEST_TEAM_ID)
        )
        if existing is not None:
            return False
        session.add(
            Workspace(
                team_id=TEST_TEAM_ID,
                bot_token_enc=b"load-test-not-a-real-token",
                key_version=1,
                installed_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return True


async def _delete_test_rows(engine, include_workspace: bool) -> None:
    """Delete only rows belonging to the test team, children before the workspace."""
    from sqlalchemy import delete

    from app.models import InboundJob, ProcessedEvent, Reminder, Task, User, Workspace

    tables = [Reminder, Task, InboundJob, ProcessedEvent, User]
    if include_workspace:
        tables.append(Workspace)
    async with engine.begin() as conn:
        for model in tables:
            await conn.execute(delete(model).where(model.team_id == TEST_TEAM_ID))


async def _count_rows(engine) -> dict:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import InboundJob, Task

    async with async_sessionmaker(engine)() as session:
        tasks = await session.scalar(
            select(func.count()).select_from(Task).where(Task.team_id == TEST_TEAM_ID)
        )
        jobs = await session.scalar(
            select(func.count())
            .select_from(InboundJob)
            .where(InboundJob.team_id == TEST_TEAM_ID)
        )
    return {"tasks": tasks, "jobs": jobs}


async def run_load_test(concurrent_requests=50) -> dict:
    """Seed, fire N commands then N events, verify against the DB, clean up.

    Returns the raw results for main() to report and judge.
    """
    _assert_safe_database(os.environ["DATABASE_URL"])
    from app.core.db import engine
    from app.main import app

    created_workspace = await _prepare_database(engine)
    run_id = uuid.uuid4().hex[:8]
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            commands = await asyncio.gather(
                *[_fire_commands_request(client, run_id, i) for i in range(concurrent_requests)]
            )
            events = await asyncio.gather(
                *[_fire_events_request(client, run_id, i) for i in range(concurrent_requests)]
            )
        counts = await _count_rows(engine)
    finally:
        await _delete_test_rows(engine, include_workspace=created_workspace)
        await engine.dispose()

    return {"commands": commands, "events": events, "counts": counts}


def _report(name: str, results: list) -> tuple[float, int]:
    """Print latency stats for one endpoint; return (p95, number of 200 responses)."""
    pct = _percentiles([latency for latency, _ in results])
    print(f"\n=== {name} ===")
    for key in ("p50", "p95", "p99", "max"):
        print(f"{key}: {pct[key]:.2f}ms")
    return pct["p95"], sum(1 for _, status in results if status == 200)


def main():
    """Run load test and print results, exiting with appropriate code."""
    parser = argparse.ArgumentParser(
        description="Load test Slack endpoints for 3-second ack budget"
    )
    parser.add_argument(
        "-n",
        "--concurrent",
        type=int,
        default=50,
        help="Number of concurrent requests per endpoint (default: 50; keep <= 100)",
    )
    args = parser.parse_args()
    n = args.concurrent

    print(
        f"Running load test: {n} /task add commands + {n} app_mention events (1:1), "
        f"{n} in flight per phase, team {TEST_TEAM_ID}, db {ALLOWED_DB_NAME}..."
    )
    result = asyncio.run(run_load_test(n))

    commands_p95, commands_ok = _report("/slack/commands (/task add)", result["commands"])
    events_p95, events_ok = _report("/slack/events (app_mention)", result["events"])

    counts = result["counts"]
    total = len(result["commands"]) + len(result["events"])
    print("\n=== database verification ===")
    print(f"200 responses:  {commands_ok + events_ok} of {total}")
    print(f"tasks created:  {counts['tasks']} (successful /task add responses: {commands_ok})")
    print(f"jobs created:   {counts['jobs']} (successful events: {events_ok})")

    failures = []
    if commands_ok + events_ok != total:
        failures.append(f"{total - commands_ok - events_ok} of {total} responses were not 200")
    if counts["tasks"] != commands_ok:
        failures.append(f"tasks created {counts['tasks']} != successful /task add {commands_ok}")
    if counts["jobs"] != events_ok:
        failures.append(f"jobs created {counts['jobs']} != successful events {events_ok}")
    max_p95 = max(commands_p95, events_p95)
    if max_p95 >= THRESHOLD_MS:
        failures.append(f"p95 ({max_p95:.2f}ms) >= {THRESHOLD_MS}ms")

    if failures:
        for failure in failures:
            print(f"\n✗ FAIL: {failure}")
        sys.exit(1)
    print(f"\n✓ PASS: all checks ok, p95 ({max_p95:.2f}ms) < {THRESHOLD_MS}ms")
    sys.exit(0)


if __name__ == "__main__":
    main()
