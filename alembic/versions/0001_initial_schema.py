"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-05

Matches ARCHITECTURE.md's data model exactly. `team_id` is denormalised onto
`reminders` and `meeting_participants` deliberately — see that doc for why.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("team_id", sa.String(), primary_key=True),
        sa.Column("bot_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uninstalled_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "users",
        sa.Column("slack_user_id", sa.String(), primary_key=True),
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("workspaces.team_id"), nullable=False
        ),
        sa.Column("tz", sa.String(), nullable=False),
        sa.Column("google_refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.Integer(), nullable=False),
    )
    op.create_index("ix_users_team_id", "users", ["team_id"])

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("workspaces.team_id"), nullable=False
        ),
        sa.Column("creator_slack_id", sa.String(), nullable=False),
        sa.Column("assignee_slack_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("due_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("channel_id", sa.String(), nullable=False),
    )
    op.create_index("ix_tasks_team_id_status", "tasks", ["team_id", "status"])

    op.create_table(
        "reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("workspaces.team_id"), nullable=False
        ),
        sa.Column("fire_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_reminders_fire_at_sent_at", "reminders", ["fire_at_utc", "sent_at"])

    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("workspaces.team_id"), nullable=False
        ),
        sa.Column("organiser_slack_id", sa.String(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("google_event_id", sa.String(), nullable=True),
    )
    op.create_index("ix_meetings_team_id", "meetings", ["team_id"])

    op.create_table(
        "meeting_participants",
        sa.Column(
            "meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id"), primary_key=True
        ),
        sa.Column(
            "slack_user_id",
            sa.String(),
            sa.ForeignKey("users.slack_user_id"),
            primary_key=True,
        ),
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("workspaces.team_id"), nullable=False
        ),
    )

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(), primary_key=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("team_id", sa.String(), nullable=True),
        sa.Column("slack_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.drop_table("processed_events")
    op.drop_table("meeting_participants")
    op.drop_index("ix_meetings_team_id", table_name="meetings")
    op.drop_table("meetings")
    op.drop_index("ix_reminders_fire_at_sent_at", table_name="reminders")
    op.drop_table("reminders")
    op.drop_index("ix_tasks_team_id_status", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_users_team_id", table_name="users")
    op.drop_table("users")
    op.drop_table("workspaces")
