"""inbound jobs — the ack-and-enqueue queue for event-triggered work

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05

Discovered missing while implementing Phase 2: the original ER diagram had
`reminders` for time-triggered work but nothing for immediately-triggered work
(an @mention needing an LLM call, a meeting request needing calendar lookups).
Same FOR UPDATE SKIP LOCKED claim pattern as reminders.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbound_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id", sa.String(), sa.ForeignKey("workspaces.team_id"), nullable=False
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_inbound_jobs_status_created_at", "inbound_jobs", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_jobs_status_created_at", table_name="inbound_jobs")
    op.drop_table("inbound_jobs")
