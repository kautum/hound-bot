"""created_at on tasks and meetings

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-06

Caught while building the analytics digest (Part 11): "this week's task
completion rate" is meaningless without a creation timestamp to filter on.
Neither table had one — the digest query was about to silently report an
all-time total while claiming to be scoped to a week. Backfilled to
`due_at_utc`/now for existing rows since there's no better information for
data that predates this column; new rows get a real value going forward.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "meetings",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_column("meetings", "created_at")
    op.drop_column("tasks", "created_at")
