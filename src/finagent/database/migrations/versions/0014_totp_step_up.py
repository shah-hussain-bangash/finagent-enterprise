"""Local authenticator factors and session-bound grants. Revision 0014, parent 0013."""

import sqlalchemy as sa
from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "totp_factors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("principal_id", sa.UUID(), nullable=False),
        sa.Column("encrypted_secret", sa.String(300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_counter", sa.BigInteger(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_totp_factors_tenant_id"),
        sa.UniqueConstraint("tenant_id", "principal_id", name="uq_totp_factors_principal"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id", "principal_id"], ["principals.tenant_id", "principals.id"]),
        sa.CheckConstraint(
            "version > 0 AND last_counter >= -1 AND failed_attempts >= 0", name="ck_totp_factor_counters"
        ),
    )
    op.create_table(
        "totp_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("password_session_id", sa.UUID(), nullable=False),
        sa.Column("factor_id", sa.UUID(), nullable=False),
        sa.Column("factor_version", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_totp_grants_tenant_id"),
        sa.UniqueConstraint("password_session_id", name="uq_totp_grants_session"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "password_session_id"], ["password_sessions.tenant_id", "password_sessions.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "factor_id"], ["totp_factors.tenant_id", "totp_factors.id"]),
        sa.CheckConstraint(
            "expires_at > verified_at AND expires_at <= verified_at + interval '5 minutes'",
            name="ck_totp_grant_window",
        ),
    )
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("totp_grants")
    op.drop_table("totp_factors")
