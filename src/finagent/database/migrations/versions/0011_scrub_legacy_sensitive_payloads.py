"""Scrub historical sensitive values from legacy parent columns.

Revision ID: 0011
Revises: 0010

Sensitive content is retained only in the purgeable payload tables.
Identity, hashes, provenance, timestamps, and relational lineage remain
on the parent records.

This migration is intentionally irreversible.
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Refuse to scrub anything unless every legacy inline value already has
    # a corresponding payload copy.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM document_extractions de
                WHERE de.segments IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM document_extraction_payloads dep
                      WHERE dep.tenant_id = de.tenant_id
                        AND dep.extraction_id = de.id
                  )
            ) THEN
                RAISE EXCEPTION
                    '0011 refused: document_extractions contains inline segments without payload';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM document_fields df
                WHERE df.value_text IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM document_field_payloads dfp
                      WHERE dfp.tenant_id = df.tenant_id
                        AND dfp.field_id = df.id
                  )
            ) THEN
                RAISE EXCEPTION
                    '0011 refused: document_fields contains inline value_text without payload';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM evidence_items ei
                WHERE ei.content IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM evidence_item_payloads eip
                      WHERE eip.tenant_id = ei.tenant_id
                        AND eip.evidence_item_id = ei.id
                  )
            ) THEN
                RAISE EXCEPTION
                    '0011 refused: evidence_items contains inline content without payload';
            END IF;
        END
        $$;
        """
    )

    # document_extractions is protected by the immutable trigger created in
    # 0005. Disable only this specific trigger for the controlled migration
    # scrub, then restore it immediately.
    op.execute("ALTER TABLE document_extractions DISABLE TRIGGER document_extractions_immutable")

    try:
        op.execute(
            """
            UPDATE document_extractions de
            SET segments = NULL
            WHERE de.segments IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM document_extraction_payloads dep
                  WHERE dep.tenant_id = de.tenant_id
                    AND dep.extraction_id = de.id
              )
            """
        )
    finally:
        op.execute("ALTER TABLE document_extractions ENABLE TRIGGER document_extractions_immutable")

    # These parent tables do not use the 0005 immutable trigger. Scrub only
    # rows whose payload copies were verified above.
    op.execute(
        """
        UPDATE document_fields df
        SET value_text = NULL
        WHERE df.value_text IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM document_field_payloads dfp
              WHERE dfp.tenant_id = df.tenant_id
                AND dfp.field_id = df.id
          )
        """
    )

    op.execute(
        """
        UPDATE evidence_items ei
        SET content = NULL
        WHERE ei.content IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM evidence_item_payloads eip
              WHERE eip.tenant_id = ei.tenant_id
                AND eip.evidence_item_id = ei.id
          )
        """
    )

    # Strong postcondition: no legacy sensitive parent values may remain.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM document_extractions
                WHERE segments IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    '0011 postcondition failed: document_extractions.segments remains';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM document_fields
                WHERE value_text IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    '0011 postcondition failed: document_fields.value_text remains';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM evidence_items
                WHERE content IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    '0011 postcondition failed: evidence_items.content remains';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0011 is intentionally irreversible: scrubbed sensitive plaintext "
        "must not be reconstructed into legacy parent columns"
    )
