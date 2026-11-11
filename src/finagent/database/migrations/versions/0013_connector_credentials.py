"""Short-lived local synthetic connector credentials, no role grants.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_credentials",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("principal_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_connector_credentials_tenant_id"),
        sa.UniqueConstraint("token_hash", name="uq_connector_credentials_token_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        sa.CheckConstraint("scope = 'alert:ingest'", name="ck_connector_credential_scope"),
        sa.CheckConstraint("expires_at > created_at", name="ck_connector_credential_window"),
    )
    op.create_index(
        "ix_connector_credentials_principal_expiry",
        "connector_credentials",
        ["tenant_id", "principal_id", "expires_at"],
    )
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("connector_credentials")
