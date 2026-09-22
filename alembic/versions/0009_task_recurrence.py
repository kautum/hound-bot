"""Add recurrence_interval_days column to tasks table

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22

Adds a nullable recurrence_interval_days column to the tasks table to support
recurring tasks. When set, marking a task done automatically creates the next
occurrence with the same title, assignee, and recurrence interval.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("recurrence_interval_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "recurrence_interval_days")
