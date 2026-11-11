"""Make legacy sensitive parent columns nullable.

Revision ID: 0010
Revises: 0009

This revision is intentionally non-destructive.
Historical sensitive parent values are not scrubbed here.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "document_extractions",
        "segments",
        existing_type=postgresql.JSONB(),
        nullable=True,
    )

    op.alter_column(
        "evidence_items",
        "content",
        existing_type=postgresql.JSONB(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM document_extractions
                WHERE segments IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0010: document_extractions contains payload-only rows';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM evidence_items
                WHERE content IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0010: evidence_items contains payload-only rows';
            END IF;
        END
        $$;
        """
    )

    op.alter_column(
        "evidence_items",
        "content",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )

    op.alter_column(
        "document_extractions",
        "segments",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )
