"""Slack Block Kit builders."""

from app.models.task import Task

# Slack allows 50 blocks per message and 100 in App Home. Each task is 2
# blocks (section + actions), plus one overflow context block: 2 * 24 + 1 = 49,
# so one cap fits both surfaces.
MAX_TASKS_SHOWN = 24


def escape_mrkdwn(text: str) -> str:
    """Escapes the three characters Slack treats as control characters in
    mrkdwn (`&`, `<`, `>`), in Slack's documented order, so user-controlled
    text can't form a live `<!channel>`, `<@U...>` or `<url|label>`. Apply
    exactly once, at the point of interpolation; never to already-escaped text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_task_block(task: Task) -> list[dict]:
    """Build a section + actions block pair for a single task.

    Shared between task list views and App Home — same shape, same button.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{escape_mrkdwn(task.title)}* — due {task.due_at_utc.isoformat()}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Mark done"},
                    "action_id": "task_done",
                    "value": str(task.id),
                }
            ],
        },
    ]


def _build_overflow_block(hidden_count: int) -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"…and {hidden_count} more open tasks"}],
    }


def _build_capped_task_blocks(tasks: list[Task], total_open: int | None) -> list[dict]:
    """At most MAX_TASKS_SHOWN tasks, then one "…and N more open tasks" block.
    `total_open` is the true number of open tasks when the caller fetched only
    a page of them; it defaults to len(tasks)."""
    shown = tasks[:MAX_TASKS_SHOWN]
    total = max(total_open if total_open is not None else 0, len(tasks))
    blocks: list[dict] = []
    for task in shown:
        blocks.extend(_build_task_block(task))
    if total > len(shown):
        blocks.append(_build_overflow_block(total - len(shown)))
    return blocks


def build_task_list_blocks(tasks: list[Task], total_open: int | None = None) -> list[dict]:
    """Build Slack Block Kit blocks for a task list.

    Each task becomes a section block showing title + due date, with an
    attached "Mark done" button in an actions block. Capped at
    MAX_TASKS_SHOWN tasks; the rest are summarised in one context block.
    """
    return _build_capped_task_blocks(tasks, total_open)


def build_app_home_view(tasks: list[Task], total_open: int | None = None) -> dict:
    """Build a Slack App Home tab view showing the user's open tasks.

    Returns a full home tab view object with type "home" and blocks array.
    Each task has an inline "Mark done" button, capped at MAX_TASKS_SHOWN. If
    no open tasks exist, shows a message instead of an empty list.
    """
    if not tasks:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No open tasks assigned to you.",
                },
            }
        ]
    else:
        blocks = _build_capped_task_blocks(tasks, total_open)

    return {"type": "home", "blocks": blocks}


def build_meeting_action_blocks(meeting_id: str, action_id: str, button_text: str) -> list[dict]:
    """Build Slack Block Kit blocks for a meeting action button.

    Returns a section block with the meeting ID and an actions block
    containing a single button. Used for both "Book" and "Cancel" actions.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Meeting ID: `{escape_mrkdwn(meeting_id)}`",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": button_text},
                    "action_id": action_id,
                    "value": meeting_id,
                }
            ],
        },
    ]
