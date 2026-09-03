"""Merge quote_cache and bank_connections branches.

Revision ID: 38926becbff8
Revises: 22f31e851581, 9b7bdd7ac58f
Create Date: 2026-08-30 04:22:59.316720
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "38926becbff8"
down_revision: tuple[str, str] = ("22f31e851581", "9b7bdd7ac58f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op merge of quote_cache and bank_connections branches."""
    pass


def downgrade() -> None:
    """No-op merge of quote_cache and bank_connections branches."""
    pass
