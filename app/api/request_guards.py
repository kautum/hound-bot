"""Shared request-boundary guards for the three signed Slack routes: a body
size cap that runs before any signature work, strict decoding of the body,
and the installed-workspace check. Every rejection is generic; request
content and exception text are never echoed back or logged.
"""

import json
import logging
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.workspace_repository import WorkspaceRepository

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_048_576
MAX_EVENT_ID_LENGTH = 128
MAX_FORM_FIELDS = 100

BAD_REQUEST_DETAIL = "invalid request"
NOT_INSTALLED_TEXT = "Hound isn't installed in this workspace. Ask a workspace admin to install it."


def bad_request(reason: str) -> HTTPException:
    """A generic 400. `reason` is a fixed internal label (never request
    content) and goes to the warning log only."""
    logger.warning("rejected malformed Slack request: %s", reason)
    return HTTPException(status_code=400, detail=BAD_REQUEST_DETAIL)


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail="request body too large")


async def read_limited_body(request: Request) -> bytes:
    """Read the raw body, answering 413 if Content-Length or the bytes actually
    streamed exceed MAX_BODY_BYTES. Called before signature verification so an
    unauthenticated caller cannot make us buffer or HMAC an unbounded body."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_length = int(declared)
        except ValueError:
            raise bad_request("non-numeric content-length") from None
        if declared_length > MAX_BODY_BYTES:
            raise _too_large()

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def contains_nul(text: str) -> bool:
    """True for a literal NUL or a JSON `\\u0000` escape. Postgres text cannot
    store NUL, so it must be stopped at the boundary."""
    return "\x00" in text or "\\u0000" in text


def decode_utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise bad_request("body is not valid utf-8") from None


def parse_json_object(text: str, label: str) -> dict:
    if contains_nul(text):
        raise bad_request(f"{label} contains NUL")
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        raise bad_request(f"{label} is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise bad_request(f"{label} is not a JSON object")
    return parsed


def parse_form(raw: bytes) -> dict[str, str]:
    """Parse an application/x-www-form-urlencoded body strictly. The first value
    of a repeated key wins (as Starlette's form.get does); any NUL byte is a 400."""
    text = decode_utf8(raw)
    try:
        pairs = parse_qsl(
            text, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS, errors="strict"
        )
    except UnicodeDecodeError:
        raise bad_request("form field is not valid utf-8") from None
    except ValueError:
        raise bad_request("form body has too many fields") from None
    fields: dict[str, str] = {}
    for key, value in pairs:
        if "\x00" in key or "\x00" in value:
            raise bad_request("form field contains NUL")
        fields.setdefault(key, value)
    return fields


async def is_workspace_installed(session: AsyncSession, team_id: str) -> bool:
    """A missing workspace row and one with uninstalled_at set are both 'not installed'."""
    workspace = await WorkspaceRepository(session).get(team_id)
    return workspace is not None and workspace.uninstalled_at is None
