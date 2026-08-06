"""Create sync_states table.

Revision ID: a5d1b5e3f4a0
Revises: b8c83ca7aa53
Create Date: 2026-08-02 11:08:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5d1b5e3f4a0"
down_revision: str | None = "b8c83ca7aa53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sync_states table."""
    op.create_table(
        "sync_states",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Drop sync_states table."""
    op.drop_table("sync_states")
