"""users primary key becomes (team_id, slack_user_id)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08

S2 from the security audit: Slack user IDs are unique only *within* a
workspace, not globally — two different organisations can have a member
with the same Slack user ID. `users` had `slack_user_id` alone as its
primary key, which meant the database itself enforced global uniqueness,
directly contradicting the multi-tenant model no matter how carefully the
repository layer filtered on team_id. Writing the tenant-isolation test for
UserRepository surfaced this immediately: two workspaces "sharing" a Slack
user ID hit a Postgres UniqueViolationError on insert, not a data leak — but
only because the schema made the leak impossible to insert around, not
because it enforced the correct thing. This migration makes it correct.

meeting_participants.slack_user_id had a single-column FK to
users.slack_user_id; it's rebuilt as a composite FK on (team_id,
slack_user_id), which also happens to guarantee a participant row's team_id
can never diverge from the linked user's team_id.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("meeting_participants_slack_user_id_fkey", "meeting_participants", type_="foreignkey")
    op.drop_constraint("users_pkey", "users", type_="primary")
    op.create_primary_key("users_pkey", "users", ["team_id", "slack_user_id"])
    op.create_foreign_key(
        "meeting_participants_team_id_slack_user_id_fkey",
        "meeting_participants",
        "users",
        ["team_id", "slack_user_id"],
        ["team_id", "slack_user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "meeting_participants_team_id_slack_user_id_fkey",
        "meeting_participants",
        type_="foreignkey",
    )
    op.drop_constraint("users_pkey", "users", type_="primary")
    op.create_primary_key("users_pkey", "users", ["slack_user_id"])
    op.create_foreign_key(
        "meeting_participants_slack_user_id_fkey",
        "meeting_participants",
        "users",
        ["slack_user_id"],
        ["slack_user_id"],
    )
