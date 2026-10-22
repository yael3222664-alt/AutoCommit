"""Baseline schema: page records and their manual edit audit trail.

Identical to the tables `Base.metadata.create_all` created before migrations existed, so
existing databases can be stamped with this revision.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-20
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requires_manual_review", sa.Boolean(), nullable=False),
        sa.Column("sender", sa.String(255), nullable=True),
        sa.Column("id_number", sa.String(32), nullable=True),
        sa.Column("received_date", sa.String(10), nullable=True),
        sa.Column("data", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("document_id", "page_number", name="uq_records_document_page"),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'extraction_failed')", name="ck_records_status"
        ),
        sa.CheckConstraint("page_number >= 1", name="ck_records_page_number"),
    )
    for column in ("document_id", "status", "requires_manual_review", "sender", "created_at"):
        op.create_index(f"ix_records_{column}", "records", [column])

    op.create_table(
        "record_edits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_id", sa.String(36), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("old_value", _JSON, nullable=True),
        sa.Column("new_value", _JSON, nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_record_edits_record_id", "record_edits", ["record_id"])


def downgrade() -> None:
    op.drop_table("record_edits")
    op.drop_table("records")
