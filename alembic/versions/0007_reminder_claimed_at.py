"""claimed_at on reminders

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08

S8 from the security audit: reminders' claim query (FOR UPDATE SKIP LOCKED)
had no way to mark a row as claimed-but-not-yet-sent, so the worker's
per-reminder commit — which ends the claiming transaction and releases the
lock on the rest of the batch — let a second worker re-claim and re-send
the same reminder. inbound_jobs already had a claimed_at column for the
same purpose; reminders never got one.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reminders", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("reminders", "claimed_at")
