"""Add full text search index to chunks

Revision ID: 8fe7466e87d9
Revises: 9fc059ca10c7
Create Date: 2026-05-22 04:09:51.165800

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import pgvector


# revision identifiers, used by Alembic.
revision: str = '8fe7466e87d9'
down_revision: Union[str, Sequence[str], None] = '9fc059ca10c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE INDEX chunks_search_idx ON chunks USING GIN(to_tsvector('english', content));")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX chunks_search_idx;")
