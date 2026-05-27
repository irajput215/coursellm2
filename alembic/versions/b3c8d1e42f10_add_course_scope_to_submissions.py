"""add course scope to submissions

Revision ID: b3c8d1e42f10
Revises: a2b5e028ce8e
Create Date: 2026-05-26 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c8d1e42f10"
down_revision: Union[str, Sequence[str], None] = "a2b5e028ce8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("submissions", sa.Column("course_id", sa.Integer(), nullable=True))
    op.add_column("submissions", sa.Column("user_id", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE submissions AS s
        SET course_id = d.course_id,
            user_id = d.user_id
        FROM documents AS d
        WHERE s.document_id = d.id
        """
    )

    op.alter_column("submissions", "course_id", nullable=False)
    op.alter_column("submissions", "user_id", nullable=False)
    op.alter_column("submissions", "document_id", existing_type=sa.Integer(), nullable=True)

    op.create_foreign_key(
        "fk_submissions_course_id_courses",
        "submissions",
        "courses",
        ["course_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_submissions_user_id_users",
        "submissions",
        "users",
        ["user_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_submissions_course_id"), "submissions", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_submissions_user_id"), "submissions", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_submissions_user_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_course_id"), table_name="submissions")
    op.drop_constraint("fk_submissions_user_id_users", "submissions", type_="foreignkey")
    op.drop_constraint("fk_submissions_course_id_courses", "submissions", type_="foreignkey")
    op.alter_column("submissions", "document_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("submissions", "user_id")
    op.drop_column("submissions", "course_id")
