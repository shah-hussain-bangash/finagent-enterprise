"""Provisioned password identities and revocable password sessions.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_credentials",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("principal_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        sa.UniqueConstraint("tenant_id", "id", name="uq_password_credentials_tenant_id"),
        sa.UniqueConstraint("email", name="uq_password_credentials_email"),
        sa.UniqueConstraint("principal_id", name="uq_password_credentials_principal"),
        sa.CheckConstraint("version > 0 AND failed_attempts >= 0", name="ck_password_credential_counters"),
    )
    op.create_table(
        "password_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("credential_id", sa.UUID(), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"], ["password_credentials.tenant_id", "password_credentials.id"]
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_password_sessions_tenant_id"),
        sa.UniqueConstraint("token_hash", name="uq_password_sessions_token_hash"),
    )
    op.create_index("ix_password_sessions_expiry", "password_sessions", ["expires_at"])
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("password_sessions")
    op.drop_table("password_credentials")
