"""proposed_start_utc on meetings

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06

Caught while wiring up an actual "book this meeting" path: propose_meeting
returned a slot time but never persisted it on the Meeting row, so a later
booking action would have had nothing to book against without re-running
the entire availability search (and possibly getting a different answer).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meetings", sa.Column("proposed_start_utc", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("meetings", "proposed_start_utc")
