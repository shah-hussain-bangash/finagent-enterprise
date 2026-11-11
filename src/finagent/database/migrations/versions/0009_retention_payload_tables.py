"""Purgeable retention payload tables.

Revision ID: 0009
Revises: 0008

Additive only. Existing source columns remain until dual-write,
backfill and read cutover are independently verified.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from finagent.database.grants import apply_app_grants

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_extraction_payloads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("extraction_id", sa.UUID(), nullable=False),
        sa.Column(
            "segments",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "extraction_id"],
            [
                "document_extractions.tenant_id",
                "document_extractions.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_document_extraction_payloads_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "extraction_id",
            name="uq_document_extraction_payload",
        ),
    )

    op.create_index(
        "ix_document_extraction_payload_extraction",
        "document_extraction_payloads",
        ["tenant_id", "extraction_id"],
    )

    op.create_table(
        "document_field_payloads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("field_id", sa.UUID(), nullable=False),
        sa.Column(
            "value_text",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "field_id"],
            [
                "document_fields.tenant_id",
                "document_fields.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_document_field_payloads_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "field_id",
            name="uq_document_field_payload",
        ),
    )

    op.create_index(
        "ix_document_field_payload_field",
        "document_field_payloads",
        ["tenant_id", "field_id"],
    )

    op.create_table(
        "evidence_item_payloads",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("evidence_item_id", sa.UUID(), nullable=False),
        sa.Column(
            "content",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "evidence_item_id"],
            [
                "evidence_items.tenant_id",
                "evidence_items.id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_evidence_item_payloads_tenant_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "evidence_item_id",
            name="uq_evidence_item_payload",
        ),
    )

    op.create_index(
        "ix_evidence_item_payload_item",
        "evidence_item_payloads",
        ["tenant_id", "evidence_item_id"],
    )

    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("evidence_item_payloads")
    op.drop_table("document_field_payloads")
    op.drop_table("document_extraction_payloads")
