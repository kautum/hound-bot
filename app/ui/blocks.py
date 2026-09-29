"""Slack Block Kit builders."""

from app.models.task import Task


def _build_task_block(task: Task) -> list[dict]:
    """Build a section + actions block pair for a single task.

    Shared between task list views and App Home — same shape, same button.
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{task.title}* — due {task.due_at_utc.isoformat()}",
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


def build_task_list_blocks(tasks: list[Task]) -> list[dict]:
    """Build Slack Block Kit blocks for a task list.

    Each task becomes a section block showing title + due date, with an
    attached "Mark done" button in an actions block.
    """
    blocks = []
    for task in tasks:
        blocks.extend(_build_task_block(task))
    return blocks


def build_app_home_view(tasks: list[Task]) -> dict:
    """Build a Slack App Home tab view showing the user's open tasks.

    Returns a full home tab view object with type "home" and blocks array.
    Each task has an inline "Mark done" button. If no open tasks exist,
    shows a message instead of an empty list.
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
        blocks = []
        for task in tasks:
            blocks.extend(_build_task_block(task))

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
                "text": f"Meeting ID: `{meeting_id}`",
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
