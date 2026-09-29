"""Add index on processed_events.processed_at and clean up old rows

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-21

Adds an index on processed_events.processed_at to support efficient 7-day
retention sweeps, and performs a one-time cleanup of rows older than 7 days
at migration-apply time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the index first
    op.create_index(
        "ix_processed_events_processed_at",
        "processed_events",
        ["processed_at"],
    )

    # One-time cleanup: delete rows older than 7 days
    op.execute(
        "DELETE FROM processed_events WHERE processed_at < NOW() - INTERVAL '7 days'"
    )


def downgrade() -> None:
    # Remove the index
    op.drop_index("ix_processed_events_processed_at", "processed_events")

    # Downgrade doesn't restore deleted rows — that's acceptable for a
    # retention sweep's one-time historical cleanup.
