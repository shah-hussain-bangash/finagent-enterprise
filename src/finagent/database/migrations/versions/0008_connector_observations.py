"""Immutable content-free connector observations for operational reporting.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(8), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source IN ('core-banking','OTHER')", name="ck_connector_observation_source"),
        sa.CheckConstraint("operation IN ('JSON','BYTES')", name="ck_connector_observation_operation"),
        sa.CheckConstraint("outcome IN ('SUCCEEDED','FAILED')", name="ck_connector_observation_outcome"),
        sa.CheckConstraint("attempts >= 0 AND latency_ms >= 0", name="ck_connector_observation_counts"),
    )
    op.create_index(
        "ix_connector_observation_time", "connector_observations", ["tenant_id", "source", "created_at"]
    )
    op.execute(
        "CREATE TRIGGER connector_observations_immutable BEFORE UPDATE OR DELETE ON connector_observations "
        "FOR EACH ROW EXECUTE FUNCTION finagent_immutable_row()"
    )
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("connector_observations")
