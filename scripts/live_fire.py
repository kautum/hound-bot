#!/usr/bin/env python3
"""Live-fire driver for external API calls.

This script drives each real external call once and prints the ACTUAL response body.
It exists because mocks hid a real bug (the userinfo.email scope miss).

Usage:
    python scripts/live_fire.py --dry-run    # Print plan, no network calls
    python scripts/live_fire.py --step 1     # Run step 1 only
    python scripts/live_fire.py              # Run all steps

Requirements:
    - All environment variables must be set (DATABASE_URL, SLACK_SIGNING_SECRET, etc.)
    - The app must be running locally with ngrok (PUBLIC_BASE_URL points to it)
    - A real Slack workspace must be installed with the bot
    - A Google account must be linked (kpkautum2643@gmail.com)
"""

import argparse
import asyncio
import hashlib
import hmac
import httpx
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

# Add parent directory to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.security import TokenCipher, token_cipher_from_settings
from app.calendar.google_calendar import GoogleCalendarProvider
from app.models.task import Task
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.task_repository import TaskRepository
from app.repositories.user_repository import UserRepository
from app.repositories.workspace_repository import WorkspaceRepository
from sqlalchemy import select


# Test values - these should match the real installed workspace
# From PROJECT-WIKI.md: team_id=T0C077J6873 for "Devin Bot" workspace
TEST_TEAM_ID = os.environ.get("TEST_TEAM_ID", "T0C077J6873")
TEST_USER_ID = os.environ.get("TEST_USER_ID", "U0123456789")  # Replace with real user ID
TEST_CHANNEL_ID = os.environ.get("TEST_CHANNEL_ID", "C0123456789")  # Replace with real channel
TEST_UNLINKED_USER_ID = os.environ.get("TEST_UNLINKED_USER_ID", "U0987654321")  # Second user for /meet


def print_step_header(step_num: int, description: str) -> None:
    """Print a step header with clear separator."""
    print(f"\n{'='*60}")
    print(f"STEP {step_num}: {description}")
    print(f"{'='*60}\n")


def print_response(label: str, response: Any) -> None:
    """Print a response body as raw JSON, never a summary."""
    print(f"{label}:")
    if isinstance(response, (dict, list)):
        print(json.dumps(response, indent=2, default=str))
    else:
        print(response)
    print()


def dry_run_plan() -> None:
    """Print the plan without making any network calls."""
    print("DRY RUN PLAN - No network calls will be made\n")
    print("This script will drive the following real external calls:\n")
    
    print("1. Signed /task add through public URL")
    print("   - Create a Slack-signed request to /slack/commands")
    print("   - Task due 30 seconds in the future")
    print("   - Assert the row exists in Postgres")
    print("   - Print the response body\n")
    
    print("2. Internal tick for reminder firing")
    print("   - Call /internal/tick with CRON_SHARED_SECRET")
    print("   - Process due reminders")
    print("   - Print the response body (reminders_sent count)\n")
    
    print("3. Google freeBusy query")
    print("   - Fetch stored refresh token from database")
    print("   - Call Google Calendar freeBusy API")
    print("   - Print the raw response body\n")
    
    print("4. /meet propose with capture endpoint")
    print("   - Start local capture endpoint")
    print("   - Send /meet propose with response_url pointing to capture")
    print("   - Print what would have been posted to response_url")
    print("   - Print instructions for running /meet in real Slack\n")
    
    print("5. /meet book against proposal")
    print("   - Use meeting ID from step 4")
    print("   - Call /meet book via signed request")
    print("   - Print the created Google event ID\n")
    
    print("Requirements:")
    print(f"  - PUBLIC_BASE_URL: {settings.public_base_url}")
    print(f"  - TEST_TEAM_ID: {TEST_TEAM_ID}")
    print(f"  - TEST_USER_ID: {TEST_USER_ID}")
    print(f"  - TEST_CHANNEL_ID: {TEST_CHANNEL_ID}")
    print(f"  - TEST_UNLINKED_USER_ID: {TEST_UNLINKED_USER_ID}")
    print()


def sign_slack_request(body: bytes, timestamp: str | None = None) -> dict[str, str]:
    """Create Slack signature headers for a request body."""
    if not settings.slack_signing_secret:
        raise RuntimeError("SLACK_SIGNING_SECRET is not configured")
    
    ts = timestamp or str(int(time.time()))
    basestring = f"v0:{ts}:".encode() + body
    digest = hmac.new(settings.slack_signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/json",
    }


def sign_slack_form(form_data: dict[str, str]) -> tuple[dict[str, str], bytes]:
    """Create Slack signature headers for form-encoded data.
    
    Returns:
        tuple of (headers dict, encoded body bytes) - the body bytes are exactly
        what was signed, so they must be sent as-is to ensure signature validity.
    """
    if not settings.slack_signing_secret:
        raise RuntimeError("SLACK_SIGNING_SECRET is not configured")
    
    # URL-encode the form data exactly as httpx would do it
    encoded_body = urlencode(form_data).encode("utf-8")
    headers = sign_slack_request(encoded_body)
    # Override Content-Type to match what we're sending
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    return headers, encoded_body


async def step1_task_add() -> dict[str, Any]:
    """Step 1: Signed /task add through public URL."""
    print_step_header(1, "Signed /task add through public URL")
    
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is not configured")
    
    # Calculate due time 30 seconds in the future
    due_at = datetime.now(UTC) + timedelta(seconds=30)
    
    # Build the /task add command
    form_data = {
        "command": "/task",
        "text": f"add <@{TEST_USER_ID}> Test task from live fire | {due_at.isoformat()}",
        "team_id": TEST_TEAM_ID,
        "user_id": TEST_USER_ID,
        "channel_id": TEST_CHANNEL_ID,
        "response_url": f"{settings.public_base_url}/slack/response",
    }
    
    print(f"Request body: {form_data}")
    print(f"Due time: {due_at.isoformat()}")
    print()
    
    # Sign the request and get the pre-encoded body
    headers, encoded_body = sign_slack_form(form_data)
    print(f"Headers: {headers}")
    print(f"Encoded body (first 200 chars): {encoded_body[:200].decode('utf-8')}")
    print()
    
    # Verify signature by recomputing HMAC over the exact bytes we'll send
    ts = headers["X-Slack-Request-Timestamp"]
    basestring = f"v0:{ts}:".encode() + encoded_body
    verify_digest = hmac.new(settings.slack_signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    print(f"Signature verification: v0={verify_digest}")
    print(f"Matches header signature: {verify_digest == headers['X-Slack-Signature'][3:]}")
    print()
    
    # Make the request with the exact pre-encoded body
    url = f"{settings.public_base_url}/slack/commands"
    print(f"POST {url}")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, content=encoded_body, headers=headers)
        print_response("Response status", response.status_code)
        print_response("Response body", response.json())
    
    # Assert the row exists in Postgres
    async with async_session_factory() as session:
        repo = TaskRepository(session, TEST_TEAM_ID)
        # Wait a moment for the task to be created
        await asyncio.sleep(1)
        tasks = await repo.list(assignee_slack_id=TEST_USER_ID)
        
        print(f"Tasks found in database: {len(tasks)}")
        for task in tasks:
            print(f"  - {task.id}: {task.title} (due {task.due_at_utc.isoformat()})")
        
        if not tasks:
            print("ERROR: No tasks found in database!")
            return {"error": "No tasks found"}
        
        # Find the task we just created
        live_fire_task = None
        for task in tasks:
            if "Test task from live fire" in task.title:
                live_fire_task = task
                break
        
        if live_fire_task:
            print(f"\n✓ Found live fire task: {live_fire_task.id}")
            return {"task_id": str(live_fire_task.id), "response": response.json()}
        else:
            print("\nERROR: Live fire task not found in database")
            return {"error": "Live fire task not found"}


async def step2_internal_tick() -> dict[str, Any]:
    """Step 2: Run internal tick for reminder firing."""
    print_step_header(2, "Internal tick for reminder firing")
    
    if not settings.cron_shared_secret:
        raise RuntimeError("CRON_SHARED_SECRET is not configured")
    
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is not configured")
    
    url = f"{settings.public_base_url}/internal/tick"
    headers = {"X-Cron-Secret": settings.cron_shared_secret}
    
    print(f"POST {url}")
    print(f"Headers: X-Cron-Secret: {settings.cron_shared_secret[:10]}...")
    print()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, headers=headers)
        print_response("Response status", response.status_code)
        print_response("Response body", response.json())
    
    return response.json()


async def step3_google_freebusy() -> dict[str, Any]:
    """Step 3: Real Google freeBusy query using stored refresh token."""
    print_step_header(3, "Google freeBusy query using stored refresh token")
    
    if not (settings.google_client_id and settings.google_client_secret):
        raise RuntimeError("Google credentials are not configured")
    
    # Fetch the stored refresh token from database
    async with async_session_factory() as session:
        users = UserRepository(session, TEST_TEAM_ID)
        user = await users.get(TEST_USER_ID)
        
        if user is None:
            print(f"ERROR: User {TEST_USER_ID} not found in database")
            return {"error": "User not found"}
        
        if user.google_refresh_token_enc is None:
            print(f"ERROR: User {TEST_USER_ID} has no linked Google account")
            return {"error": "No Google account linked"}
        
        if user.google_link_broken_at:
            print(f"ERROR: User's Google link is marked as broken")
            return {"error": "Google link broken"}
        
        print(f"Found user: {TEST_USER_ID}")
        print(f"Google email: {user.google_email}")
        print(f"Key version: {user.key_version}")
        print()
        
        # Decrypt the refresh token
        cipher = token_cipher_from_settings(settings)
        refresh_token = cipher.decrypt(user.google_refresh_token_enc, user.key_version)
        print(f"Refresh token (first 20 chars): {refresh_token[:20]}...")
        print()
    
    # Make the freeBusy query
    window_start = datetime.now(UTC)
    window_end = window_start + timedelta(hours=24)
    
    print(f"Querying freeBusy from {window_start.isoformat()} to {window_end.isoformat()}")
    print()
    
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        provider = GoogleCalendarProvider(
            http_client,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        
        try:
            busy_blocks = await provider.get_busy_blocks(refresh_token, window_start, window_end)
            print_response("Busy blocks", [{"start": b.start_utc.isoformat(), "end": b.end_utc.isoformat()} for b in busy_blocks])
            return {"busy_blocks": len(busy_blocks), "data": [{"start": b.start_utc.isoformat(), "end": b.end_utc.isoformat()} for b in busy_blocks]}
        except Exception as e:
            print(f"ERROR: {e}")
            return {"error": str(e)}


class CaptureEndpoint:
    """Local capture endpoint for response_url testing."""
    
    def __init__(self):
        self.captured_payloads: list[dict] = []
        self.server = None
    
    async def start(self, port: int = 8765):
        """Start the capture server using a simple HTTP server."""
        from aiohttp import web
        
        async def capture(request):
            payload = await request.json()
            self.captured_payloads.append(payload)
            return web.json_response({"status": "captured"})
        
        app = web.Application()
        app.router.add_post("/capture", capture)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        
        self.server = runner
        print(f"Capture endpoint started on http://127.0.0.1:{port}/capture")
    
    async def stop(self):
        """Stop the capture server."""
        if self.server:
            await self.server.cleanup()


async def step4_meet_propose() -> dict[str, Any]:
    """Step 4: /meet propose with capture endpoint."""
    print_step_header(4, "/meet propose with capture endpoint")
    
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is not configured")
    
    # Start capture endpoint
    capture = CaptureEndpoint()
    await capture.start(port=8765)
    
    try:
        capture_url = "http://127.0.0.1:8765/capture"
        
        # Build /meet propose command
        # Requires 1-7 mentioned participants per requirement
        window_start = datetime.now(UTC) + timedelta(hours=1)
        window_end = window_start + timedelta(hours=4)
        
        form_data = {
            "command": "/meet",
            "text": f"<@{TEST_UNLINKED_USER_ID}> 30 | {window_start.isoformat()} | {window_end.isoformat()}",
            "team_id": TEST_TEAM_ID,
            "user_id": TEST_USER_ID,
            "channel_id": TEST_CHANNEL_ID,
            "response_url": capture_url,
        }
        
        print(f"Request body: {form_data}")
        print(f"Capture URL: {capture_url}")
        print()
        
        # Sign the request and get the pre-encoded body
        headers, encoded_body = sign_slack_form(form_data)
        
        # Make the request with the exact pre-encoded body
        url = f"{settings.public_base_url}/slack/commands"
        print(f"POST {url}")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, content=encoded_body, headers=headers)
            print_response("Immediate response", response.json())
        
        # Wait for the worker to process and post to response_url
        print("Waiting for worker to process (up to 30 seconds)...")
        for i in range(30):
            await asyncio.sleep(1)
            if capture.captured_payloads:
                break
            print(f"  {i+1}/30 seconds...")
        
        if capture.captured_payloads:
            print_response("Captured response_url payload", capture.captured_payloads[0])
            
            # Extract meeting ID if present
            payload_text = capture.captured_payloads[0].get("text", "")
            print(f"\nPayload text: {payload_text}")
            
            # Try to extract meeting ID from the response
            import re
            meeting_id_match = re.search(r'/meet book ([a-f0-9-]+)', payload_text)
            meeting_id = meeting_id_match.group(1) if meeting_id_match else None
            
            if meeting_id:
                print(f"Extracted meeting ID: {meeting_id}")
            
            result = {
                "captured": capture.captured_payloads[0],
                "meeting_id": meeting_id,
            }
        else:
            print("ERROR: No payload captured on response_url")
            result = {"error": "No payload captured"}
        
        # Print instructions for running /meet in real Slack
        print("\n" + "="*60)
        print("INSTRUCTIONS FOR RUNNING /meet IN REAL SLACK")
        print("="*60)
        print("\nTo run the same /meet command in the real Slack workspace:")
        print(f"\n1. Open Slack in the 'Devin Bot' workspace")
        print(f"2. Go to the channel where the bot is installed")
        print(f"3. Run the following command:")
        print(f"\n   /meet <@{TEST_UNLINKED_USER_ID}> 30 | {window_start.isoformat()} | {window_end.isoformat()}")
        print(f"\n4. If a slot is found, book it with:")
        print(f"   /meet book <meeting-id-from-response>")
        print(f"\nNote: Replace {TEST_UNLINKED_USER_ID} with a real Slack user ID")
        print("that exists in your workspace.\n")
        
        return result
    
    finally:
        await capture.stop()


async def step5_meet_book(meeting_id: str | None) -> dict[str, Any]:
    """Step 5: /meet book against proposal."""
    print_step_header(5, "/meet book against proposal")
    
    if not meeting_id:
        print("ERROR: No meeting ID provided (run step 4 first)")
        return {"error": "No meeting ID"}
    
    if not settings.public_base_url:
        raise RuntimeError("PUBLIC_BASE_URL is not configured")
    
    # Build /meet book command
    form_data = {
        "command": "/meet",
        "text": f"book {meeting_id}",
        "team_id": TEST_TEAM_ID,
        "user_id": TEST_USER_ID,
        "channel_id": TEST_CHANNEL_ID,
        "response_url": f"{settings.public_base_url}/slack/response",
    }
    
    print(f"Request body: {form_data}")
    print(f"Meeting ID: {meeting_id}")
    print()
    
    # Sign the request and get the pre-encoded body
    headers, encoded_body = sign_slack_form(form_data)
    
    # Make the request with the exact pre-encoded body
    url = f"{settings.public_base_url}/slack/commands"
    print(f"POST {url}")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, content=encoded_body, headers=headers)
        print_response("Immediate response", response.json())
    
    # Query the database to get the Google event ID
    async with async_session_factory() as session:
        from app.models.meeting import Meeting
        result = await session.execute(
            select(Meeting).filter_by(id=uuid.UUID(meeting_id), team_id=TEST_TEAM_ID)
        )
        meeting = result.scalar_one_or_none()
        
        if meeting:
            print(f"Meeting status: {meeting.status}")
            print(f"Google event ID: {meeting.google_event_id}")
            print(f"Proposed start: {meeting.proposed_start_utc.isoformat() if meeting.proposed_start_utc else 'None'}")
            return {
                "meeting_id": meeting_id,
                "status": meeting.status,
                "google_event_id": meeting.google_event_id,
            }
        else:
            print(f"ERROR: Meeting {meeting_id} not found in database")
            return {"error": "Meeting not found"}


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Live-fire driver for external API calls")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without making network calls")
    parser.add_argument("--step", type=int, choices=[1, 2, 3, 4, 5], help="Run only the specified step")
    args = parser.parse_args()
    
    if args.dry_run:
        dry_run_plan()
        return
    
    # Check required environment variables
    if not settings.public_base_url:
        print("ERROR: PUBLIC_BASE_URL is not configured")
        sys.exit(1)
    
    if not settings.slack_signing_secret:
        print("ERROR: SLACK_SIGNING_SECRET is not configured")
        sys.exit(1)
    
    print(f"PUBLIC_BASE_URL: {settings.public_base_url}")
    print(f"TEST_TEAM_ID: {TEST_TEAM_ID}")
    print(f"TEST_USER_ID: {TEST_USER_ID}")
    print()
    
    meeting_id = None
    
    if args.step == 1 or args.step is None:
        result1 = await step1_task_add()
        print(f"\nStep 1 complete: {result1}")
    
    if args.step == 2 or args.step is None:
        result2 = await step2_internal_tick()
        print(f"\nStep 2 complete: {result2}")
    
    if args.step == 3 or args.step is None:
        result3 = await step3_google_freebusy()
        print(f"\nStep 3 complete: {result3}")
    
    if args.step == 4 or args.step is None:
        result4 = await step4_meet_propose()
        print(f"\nStep 4 complete: {result4}")
        meeting_id = result4.get("meeting_id")
    
    if args.step == 5 or args.step is None:
        if args.step == 5 and meeting_id is None:
            # If running step 5 alone, need to get meeting ID from somewhere
            print("ERROR: Step 5 requires a meeting ID. Run step 4 first, or provide one.")
            print("For manual testing, you can query the database for a proposed meeting.")
            sys.exit(1)
        result5 = await step5_meet_book(meeting_id)
        print(f"\nStep 5 complete: {result5}")
    
    print("\n" + "="*60)
    print("LIVE FIRE COMPLETE")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
