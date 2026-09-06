"""google link broken flag + linked email

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06

`google_link_broken_at` supports invalid_grant recovery (ARCHITECTURE.md's
OAuth flows section): when Google's refresh token no longer works, this is
set instead of silently retrying or deleting the row — the user is prompted
to re-link.

`google_email` is captured at link time and used to invite the person as a
calendar attendee when booking a meeting — Google's Calendar API needs an
email per attendee, which nothing else in this schema provides.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("google_link_broken_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("google_email", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "google_email")
    op.drop_column("users", "google_link_broken_at")
