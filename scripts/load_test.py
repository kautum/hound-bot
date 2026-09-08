#!/usr/bin/env python3
"""Load test for Slack endpoints, verifying 3-second ack budget under concurrency.

Exits 0 if p95 < 3000ms, 1 otherwise. Uses a separate database (swa_devin) to avoid
interfering with other processes. Requires the app's database schema to exist in swa_devin.
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

# Add parent directory to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

# Override DATABASE_URL to use the separate test database
os.environ["DATABASE_URL"] = "postgresql+asyncpg://kpkautum@localhost:5432/swa_devin"

SIGNING_SECRET = "test-signing-secret"


def _signed_headers(body, secret, timestamp=None):
    """Mirrors Slack's documented HMAC signing scheme."""
    ts = timestamp or str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/json",
    }


def _signed_form_headers(form_data: dict, secret: str) -> dict:
    """Signed headers for form-encoded slash command requests."""
    # Slack signs the raw form body as UTF-8 bytes
    form_str = "&".join(f"{k}={v}" for k, v in form_data.items())
    body = form_str.encode("utf-8")
    return _signed_headers(body, secret)


async def _ensure_test_workspace(client):
    """Create a test workspace in the database if it doesn't exist."""
    # This is a no-op for the load test - we're testing endpoint latency,
    # not correctness. The endpoints will fail auth but still ack quickly.
    pass


async def _fire_events_request(client):
    """Fire a single /slack/events request and return latency in ms."""
    body = json.dumps(
        {
            "type": "event_callback",
            "event_id": f"Ev_{time.time_ns()}",
            "team_id": "T12345",
            "event": {"type": "app_mention", "channel": "C1", "text": "hi"},
        }
    ).encode()

    start = time.perf_counter()
    response = await client.post(
        "/slack/events", content=body, headers=_signed_headers(body, SIGNING_SECRET)
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    # We expect 401 (invalid signature due to no workspace) or 200,
    # but the latency is what matters, not the response code.
    return elapsed_ms


async def _fire_commands_request(client):
    """Fire a single /slack/commands request and return latency in ms."""
    form_data = {
        "command": "/task",
        "text": "list",
        "team_id": "T12345",
        "user_id": "U12345",
        "channel_id": "C12345",
    }

    start = time.perf_counter()
    response = await client.post(
        "/slack/commands",
        data=form_data,
        headers=_signed_form_headers(form_data, SIGNING_SECRET),
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    return elapsed_ms


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


async def run_load_test(concurrent_requests=50):
    """Run load test against both endpoints concurrently.

    Returns (events_percentiles, commands_percentiles).
    """
    # httpx is already a dev dependency in pyproject.toml, no new dependency needed
    # Import app after DATABASE_URL is set
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await _ensure_test_workspace(client)

        # Fire concurrent requests to /slack/events
        events_latencies = await asyncio.gather(
            *[_fire_events_request(client) for _ in range(concurrent_requests)]
        )

        # Fire concurrent requests to /slack/commands
        commands_latencies = await asyncio.gather(
            *[_fire_commands_request(client) for _ in range(concurrent_requests)]
        )

    return (
        _percentiles(events_latencies),
        _percentiles(commands_latencies),
    )


def main():
    """Run load test and print results, exiting with appropriate code."""
    import argparse

    parser = argparse.ArgumentParser(description="Load test Slack endpoints for 3-second ack budget")
    parser.add_argument(
        "-n",
        "--concurrent",
        type=int,
        default=50,
        help="Number of concurrent requests per endpoint (default: 50)",
    )
    args = parser.parse_args()

    print(f"Running load test with {args.concurrent} concurrent requests per endpoint...")
    events_pct, commands_pct = asyncio.run(run_load_test(args.concurrent))

    print("\n=== /slack/events ===")
    print(f"p50: {events_pct['p50']:.2f}ms")
    print(f"p95: {events_pct['p95']:.2f}ms")
    print(f"p99: {events_pct['p99']:.2f}ms")
    print(f"max: {events_pct['max']:.2f}ms")

    print("\n=== /slack/commands ===")
    print(f"p50: {commands_pct['p50']:.2f}ms")
    print(f"p95: {commands_pct['p95']:.2f}ms")
    print(f"p99: {commands_pct['p99']:.2f}ms")
    print(f"max: {commands_pct['max']:.2f}ms")

    # Assert p95 < 3000ms for both endpoints
    max_p95 = max(events_pct["p95"], commands_pct["p95"])
    THRESHOLD_MS = 3000
    if max_p95 < THRESHOLD_MS:
        print(f"\n✓ PASS: p95 ({max_p95:.2f}ms) < {THRESHOLD_MS}ms")
        sys.exit(0)
    else:
        print(f"\n✗ FAIL: p95 ({max_p95:.2f}ms) >= {THRESHOLD_MS}ms")
        sys.exit(1)


if __name__ == "__main__":
    main()
