"""store quiz misconceptions as typed objects

``quiz_attempts.misconceptions`` was a JSONB ``list[str]``: the evaluator wrote
each :class:`~coursellm.assessment.schemas.Misconception` with
``model_dump_json()``, so a consumer had to parse a JSON document *inside* a JSON
array element. The column now stores the object directly, which matches the
``rubric`` column beside it and lets the API validate the element against
``MisconceptionResponse`` instead of accepting an arbitrary string.

The upgrade converts every existing string element to the object it encodes and
leaves already-object elements untouched, so it is safe to run against a database
that is already in the new shape (and the downgrade is the exact inverse).

**No Row-Level Security change.** ``quiz_attempts`` already carries ``tenant_id``
and the ``tenant_isolation`` policy from the assessments migration; changing the
shape of one column does not touch its policy, and this revision declares no RLS
table list, so the security test's union of migration RLS lists stays equal to
the code's list of tenant-scoped tables.

Revision ID: c4f8a1d27b90
Revises: 020bc81dc009
Create Date: 2026-09-25 09:45:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4f8a1d27b90"
down_revision: str | None = "020bc81dc009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STRINGS_TO_OBJECTS = """
UPDATE quiz_attempts
SET misconceptions = COALESCE(
    (
        SELECT jsonb_agg(
            CASE
                WHEN jsonb_typeof(elem) = 'string' THEN (elem #>> '{}')::jsonb
                ELSE elem
            END
        )
        FROM jsonb_array_elements(misconceptions) AS elem
    ),
    '[]'::jsonb
)
WHERE jsonb_typeof(misconceptions) = 'array'
"""

_OBJECTS_TO_STRINGS = """
UPDATE quiz_attempts
SET misconceptions = COALESCE(
    (
        SELECT jsonb_agg(
            CASE
                WHEN jsonb_typeof(elem) = 'object' THEN to_jsonb(elem::text)
                ELSE elem
            END
        )
        FROM jsonb_array_elements(misconceptions) AS elem
    ),
    '[]'::jsonb
)
WHERE jsonb_typeof(misconceptions) = 'array'
"""


def upgrade() -> None:
    op.execute(_STRINGS_TO_OBJECTS)


def downgrade() -> None:
    op.execute(_OBJECTS_TO_STRINGS)
