"""Local/test WebAuthn factors, one-use challenges and session grants. Revision 0016."""

from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


# Frozen schema DDL, independent of future ORM models.
def upgrade() -> None:
    op.execute(
        "\nCREATE TABLE passkey_factors (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tprincipal_id UUID NOT NULL, \n\tcredential_id VARCHAR(2048) NOT NULL, \n\tpublic_key VARCHAR(4096) NOT NULL, \n\tsign_count BIGINT NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_passkey_factor_tenant UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_passkey_factor_principal UNIQUE (tenant_id, principal_id), \n\tCONSTRAINT uq_passkey_credential_id UNIQUE (credential_id), \n\tFOREIGN KEY(tenant_id, principal_id) REFERENCES principals (tenant_id, id), \n\tCONSTRAINT ck_passkey_factor_counters CHECK (version > 0 AND sign_count >= 0), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE passkey_challenges (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tpassword_session_id UUID NOT NULL, \n\tcredential_id UUID NOT NULL, \n\tcredential_version INTEGER NOT NULL, \n\tpurpose VARCHAR(12) NOT NULL, \n\tchallenge VARCHAR(100) NOT NULL, \n\trp_id VARCHAR(253) NOT NULL, \n\torigin VARCHAR(300) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tconsumed_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_passkey_challenge_tenant UNIQUE (tenant_id, id), \n\tFOREIGN KEY(tenant_id, credential_id) REFERENCES password_credentials (tenant_id, id), \n\tFOREIGN KEY(tenant_id, password_session_id) REFERENCES password_sessions (tenant_id, id), \n\tCONSTRAINT ck_passkey_challenge_purpose CHECK (purpose IN ('REGISTER','VERIFY') AND credential_version > 0), \n\tCONSTRAINT ck_passkey_challenge_window CHECK (expires_at > created_at AND expires_at <= created_at + interval '2 minutes'), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT\n)\n\n"
    )
    op.execute(
        "CREATE INDEX ix_passkey_challenge_session ON passkey_challenges (tenant_id, password_session_id, purpose)"
    )
    op.execute(
        "\nCREATE TABLE passkey_grants (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tpassword_session_id UUID NOT NULL, \n\tfactor_id UUID NOT NULL, \n\tfactor_version INTEGER NOT NULL, \n\tverified_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_passkey_grant_tenant UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_passkey_grant_session UNIQUE (password_session_id), \n\tFOREIGN KEY(tenant_id, password_session_id) REFERENCES password_sessions (tenant_id, id), \n\tFOREIGN KEY(tenant_id, factor_id) REFERENCES passkey_factors (tenant_id, id), \n\tCONSTRAINT ck_passkey_grant_window CHECK (factor_version > 0 AND expires_at > verified_at AND expires_at <= verified_at + interval '5 minutes'), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT\n)\n\n"
    )
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("passkey_grants")
    op.drop_table("passkey_challenges")
    op.drop_table("passkey_factors")
