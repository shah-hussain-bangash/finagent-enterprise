"""Due-time escalation queue (FR-011; prompt §19 supervisor escalation path; DEC-06 SLAs OPEN).

* `evaluate_due` (worker/scheduler, SYSTEM actor): for each enabled rule, cases that have sat in
  the rule's status longer than its due time get ONE escalation per status episode. The unique key
  (tenant, case, rule, episode_started_at) makes repeated or concurrent evaluation idempotent.
  Escalations of episodes that have ended are marked resolved.
* `deliver_notifications`: sends pending/failed notifications with the escalation id as the
  provider idempotency key. Retries update the SAME row; failures stay visible (FR-011).
* `list_escalations`: tenant-scoped supervisor view.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from finagent.core.correlation import get_correlation_id
from finagent.database.models import Case, Escalation, EscalationRule, OutboxEvent
from finagent.domain.states import CASE_TERMINAL
from finagent.integrations.notifications.port import Notification, NotificationError, NotificationPort
from finagent.security.authorization.policy import Permission, PrincipalContext, require
from finagent.services.audit_service import writer as audit

MAX_NOTIFICATION_ATTEMPTS = 5


def _due_at() -> Any:
    return Case.status_changed_at + func.make_interval(0, 0, 0, 0, 0, EscalationRule.due_after_minutes)


def evaluate_due(session: Session, now: datetime | None = None) -> list[uuid.UUID]:
    """Raise escalations for overdue cases across tenants. Returns the ids created by THIS call."""
    now = now or datetime.now(UTC)
    terminal = [s.value for s in CASE_TERMINAL]
    candidates = (
        select(
            func.gen_random_uuid(),
            Case.tenant_id,
            Case.id,
            EscalationRule.id,
            Case.status,
            Case.status_changed_at,
            _due_at(),
            literal(now),
            EscalationRule.owner_role,
            literal("PENDING"),
            literal(0),
        )
        .join(
            EscalationRule,
            and_(EscalationRule.tenant_id == Case.tenant_id, EscalationRule.applies_to_status == Case.status),
        )
        .where(EscalationRule.enabled.is_(True), Case.status.not_in(terminal), _due_at() <= now)
    )
    inserted = session.execute(
        pg_insert(Escalation)
        .from_select(
            [
                "id",
                "tenant_id",
                "case_id",
                "rule_id",
                "case_status",
                "episode_started_at",
                "due_at",
                "escalated_at",
                "owner_role",
                "notification_status",
                "notification_attempts",
            ],
            candidates,
        )
        .on_conflict_do_nothing(constraint="uq_escalation_once")
        .returning(Escalation.id, Escalation.tenant_id, Escalation.case_id, Escalation.owner_role)
    ).all()
    corr = get_correlation_id()
    for esc_id, tenant_id, case_id, owner_role in inserted:
        audit.record(
            session,
            tenant_id=tenant_id,
            action="CASE_ESCALATED",
            object_type="escalation",
            object_id=str(esc_id),
            outcome="SUCCESS",
            actor_principal_id=None,
            actor_kind="SYSTEM",
            case_id=case_id,
            details={"owner_role": owner_role},
        )
        session.add(
            OutboxEvent(
                tenant_id=tenant_id,
                event_type="case.escalated.v1",
                schema_version="1",
                resource_id=str(case_id),
                correlation_id=corr,
                payload={"escalation_id": str(esc_id), "case_id": str(case_id), "owner_role": owner_role},
            )
        )
    # Episodes that ended (status changed or case closed) resolve their escalations.
    session.execute(
        update(Escalation)
        .where(
            Escalation.resolved_at.is_(None),
            Escalation.tenant_id == Case.tenant_id,
            Escalation.case_id == Case.id,
            or_(
                Case.status != Escalation.case_status, Case.status_changed_at != Escalation.episode_started_at
            ),
        )
        .values(resolved_at=now)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    return [row[0] for row in inserted]


def deliver_notifications(
    session: Session, port: NotificationPort, max_attempts: int = MAX_NOTIFICATION_ATTEMPTS, batch: int = 50
) -> dict[str, int]:
    rows = session.scalars(
        select(Escalation)
        .where(Escalation.notification_status.in_(["PENDING", "FAILED"]))
        .order_by(Escalation.escalated_at)
        .limit(batch)
        .with_for_update(skip_locked=True)  # concurrent workers never double-send one row
    ).all()
    outcome = {"sent": 0, "failed": 0, "gave_up": 0}
    for esc in rows:
        esc.notification_attempts += 1
        try:
            port.send(
                Notification(
                    tenant_id=esc.tenant_id,
                    idempotency_key=f"escalation:{esc.id}",
                    recipient_role=esc.owner_role,
                    template="case.escalated",
                    resource_ref=str(esc.case_id),
                )
            )
        except NotificationError as exc:
            esc.last_notification_error = exc.code[:64]
            if not exc.transient or esc.notification_attempts >= max_attempts:
                esc.notification_status = "GAVE_UP"
                outcome["gave_up"] += 1
                audit.record(
                    session,
                    tenant_id=esc.tenant_id,
                    action="NOTIFICATION_DELIVERY_FAILED",
                    object_type="escalation",
                    object_id=str(esc.id),
                    outcome="FAILED",
                    actor_principal_id=None,
                    actor_kind="SYSTEM",
                    case_id=esc.case_id,
                    details={"error_code": exc.code, "attempts": esc.notification_attempts},
                )
            else:
                esc.notification_status = "FAILED"
                outcome["failed"] += 1
            continue
        esc.notification_status, esc.last_notification_error = "SENT", None
        outcome["sent"] += 1
    session.flush()
    return outcome


def list_escalations(
    session: Session, ctx: PrincipalContext, *, limit: int, include_resolved: bool = False
) -> dict[str, Any]:
    require(ctx, Permission.ESCALATION_READ)
    stmt = (
        select(Escalation, EscalationRule.rule_key)
        .join(
            EscalationRule,
            and_(EscalationRule.tenant_id == Escalation.tenant_id, EscalationRule.id == Escalation.rule_id),
        )
        .where(Escalation.tenant_id == ctx.tenant_id)
    )
    if not include_resolved:
        stmt = stmt.where(Escalation.resolved_at.is_(None))
    rows = session.execute(stmt.order_by(Escalation.due_at, Escalation.id).limit(limit)).all()
    return {
        "items": [
            {
                "escalation_id": str(e.id),
                "case_id": str(e.case_id),
                "rule_key": rule_key,
                "case_status": e.case_status,
                "due_at": e.due_at.isoformat(),
                "escalated_at": e.escalated_at.isoformat(),
                "owner_role": e.owner_role,
                "notification_status": e.notification_status,
                "notification_attempts": e.notification_attempts,
                "last_notification_error": e.last_notification_error,
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e, rule_key in rows
        ]
    }


def due_at_for(session: Session, case: Case) -> datetime | None:
    """Configured due time of the case's current status episode (shown on the case, FR-011)."""
    minutes = session.scalar(
        select(func.min(EscalationRule.due_after_minutes)).where(
            EscalationRule.tenant_id == case.tenant_id,
            EscalationRule.applies_to_status == case.status,
            EscalationRule.enabled.is_(True),
        )
    )
    if minutes is None:
        return None
    return case.status_changed_at + timedelta(minutes=int(minutes))
