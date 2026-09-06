"""Deterministic parsing for `/task` slash commands. Explicit syntax only —
fuzzy natural-language dates ("next Friday") are Phase 5's job, layered on
top of this deterministic tier. See ARCHITECTURE.md's tools-first-LLM-last
principle, and ENGINEERING.md's rule against a parser that guesses.
"""

import re
from datetime import UTC, datetime

MENTION_RE = re.compile(r"^<@([A-Z0-9]+)(?:\|[^>]*)?>\s*")


class TaskCommandError(ValueError):
    """The command text didn't match the expected explicit syntax."""


def parse_task_add(text: str) -> tuple[str, str, datetime]:
    """Expected syntax: `<@assignee> Title of the task | 2026-09-10T17:00+00:00`

    Returns (assignee_slack_id, title, due_at_utc). Raises TaskCommandError on
    anything else — never guesses at a missing or ambiguous piece, per the
    "fail loud, not plausible" rule for anything parsing untrusted input.
    """
    match = MENTION_RE.match(text)
    if not match:
        raise TaskCommandError(
            "Usage: /task add @assignee Title of the task | 2026-09-10T17:00+00:00"
        )
    assignee_slack_id = match.group(1)
    rest = text[match.end() :]

    if "|" not in rest:
        raise TaskCommandError(
            "Missing ' | <due date>' — an explicit ISO 8601 date/time is required"
        )
    title, _, due_raw = rest.rpartition("|")
    title = title.strip()
    due_raw = due_raw.strip()
    if not title:
        raise TaskCommandError("Task title cannot be empty")

    try:
        due_at = datetime.fromisoformat(due_raw)
    except ValueError as exc:
        raise TaskCommandError(f"Could not parse due date {due_raw!r} as ISO 8601") from exc

    if due_at.tzinfo is None:
        raise TaskCommandError(
            f"Due date {due_raw!r} has no timezone — include one explicitly "
            "(e.g. 'Z' or '+00:00'); a guessed timezone is worse than an error"
        )

    return assignee_slack_id, title, due_at.astimezone(UTC)
