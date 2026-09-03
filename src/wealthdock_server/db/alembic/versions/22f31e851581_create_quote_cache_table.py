"""Create quote cache table.

Revision ID: 22f31e851581
Revises: a5d1b5e3f4a0
Create Date: 2026-08-23 15:55:12.182519
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "22f31e851581"
down_revision: str | None = "a5d1b5e3f4a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create quote_cache table."""
    op.create_table(
        "quote_cache",
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("asset_class", sa.String(length=20), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol"),
    )


def downgrade() -> None:
    """Drop quote_cache table."""
    op.drop_table("quote_cache")
