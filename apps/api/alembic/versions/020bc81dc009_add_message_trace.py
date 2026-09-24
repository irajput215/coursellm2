"""add message trace columns

Adds the observability columns ``docs/architecture/observability.md`` section
10.1 requires on ``messages``: ``intent``, ``model``, ``prompt_version``,
``latency_ms`` and an indexed, nullable ``trace_id``. Every column is nullable
because rows written before this revision predate them and a non-agent chat turn
has no routed intent.

**No Row-Level Security change.** ``messages`` already carries ``tenant_id`` and
is already protected by the ``tenant_isolation`` policy from the initial
migration; adding columns to a protected table does not change its policy, and
this revision deliberately declares no RLS table list of its own, so the security
test's union of migration RLS lists stays exactly equal to the code's list of
tenant-scoped tables.

Revision ID: 020bc81dc009
Revises: e1c7a94b2f60
Create Date: 2026-09-25 04:41:58.418205
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "020bc81dc009"
down_revision: str | None = "e1c7a94b2f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("intent", sa.String(length=40), nullable=True))
    op.add_column("messages", sa.Column("model", sa.String(length=120), nullable=True))
    op.add_column("messages", sa.Column("prompt_version", sa.String(length=120), nullable=True))
    op.add_column("messages", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("trace_id", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_messages_trace_id"), "messages", ["trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_trace_id"), table_name="messages")
    op.drop_column("messages", "trace_id")
    op.drop_column("messages", "latency_ms")
    op.drop_column("messages", "prompt_version")
    op.drop_column("messages", "model")
    op.drop_column("messages", "intent")
