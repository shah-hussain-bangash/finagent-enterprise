"""Session-bound email possession proof, not MFA. Revision 0015, parent 0014."""

import sqlalchemy as sa
from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_verification_challenges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("credential_id", sa.UUID(), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("password_session_id", sa.UUID(), nullable=False),
        sa.Column("email_digest", sa.String(64), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("delivery_status", sa.String(10), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_email_verification_tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"], ["password_credentials.tenant_id", "password_credentials.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "password_session_id"], ["password_sessions.tenant_id", "password_sessions.id"]
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0 AND failed_attempts <= 5 AND credential_version > 0",
            name="ck_email_verification_attempts",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '5 minutes'",
            name="ck_email_verification_window",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('PENDING','SENT','FAILED')", name="ck_email_verification_delivery"
        ),
    )
    op.create_index(
        "ix_email_verification_account_created",
        "email_verification_challenges",
        ["tenant_id", "credential_id", "created_at"],
    )
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("email_verification_challenges")
