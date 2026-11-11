"""Tenant capability incident stops and immutable evidence metadata.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

from finagent.database.grants import apply_app_grants

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE capability_incidents (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tcapability_id VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\trecord_version INTEGER NOT NULL, \n\towner_role VARCHAR(32) NOT NULL, \n\tnotification_status VARCHAR(16) NOT NULL, \n\tnotification_attempts INTEGER NOT NULL, \n\tlast_notification_error VARCHAR(64), \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_capability_incident_tenant_id UNIQUE (tenant_id, id), \n\tCONSTRAINT ck_capability_incident_status CHECK (status IN ('OPEN','INVESTIGATED','RESTORED')), \n\tCONSTRAINT ck_capability_incident_version CHECK (record_version > 0), \n\tCONSTRAINT ck_capability_incident_notification CHECK (notification_status IN ('PENDING','FAILED','SENT','GAVE_UP')), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT\n)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_capability_incident_active ON capability_incidents (tenant_id, capability_id) WHERE status <> 'RESTORED'"
    )
    op.execute(
        "CREATE TABLE capability_incident_events (\n\tid UUID NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tincident_id UUID NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tactor_id UUID, \n\tdetails JSONB NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(tenant_id, incident_id) REFERENCES capability_incidents (tenant_id, id), \n\tCONSTRAINT ck_capability_incident_event_kind CHECK (kind IN ('OPENED','INVESTIGATED','RESTORED','NOTIFIED','NOTIFICATION_FAILED','NOTIFICATION_REQUEUED')), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE RESTRICT\n)"
    )
    op.execute(
        "CREATE INDEX ix_capability_incident_events ON capability_incident_events (tenant_id, incident_id, created_at)"
    )
    op.execute(
        "CREATE TRIGGER capability_incident_events_immutable BEFORE UPDATE OR DELETE ON capability_incident_events FOR EACH ROW EXECUTE FUNCTION finagent_immutable_row()"
    )
    apply_app_grants(op.execute)


def downgrade() -> None:
    op.drop_table("capability_incident_events")
    op.drop_table("capability_incidents")
