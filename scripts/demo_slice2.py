"""Vertical slice 2 (prompt §52): start run -> KILL worker mid-step -> new worker resumes from the
checkpoint -> no duplicate event or external effect. Real processes, real PostgreSQL. LOCAL ONLY.

    python scripts/demo_slice2.py        (needs `migrate db-app-role seed` and .env; see README)

Worker A is started with an injected stall right after it has sent the notification but before it
commits the step, then hard-killed (no cleanup, like `kill -9`). Worker B waits for A's lease to
expire, takes over with a higher fencing token, and finishes. The script then checks the ledger,
the notification sink, the outbox/inbox and the step records, and exits non-zero on any failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from scripts.tasks import load_env  # noqa: E402

load_env()
os.environ.setdefault("FINAGENT_ENVIRONMENT", "local")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from finagent.api.main import create_app  # noqa: E402
from finagent.core.config import get_settings  # noqa: E402
from finagent.database.models import (  # noqa: E402
    ExternalOperation,
    InboxEvent,
    Lease,
    OutboxEvent,
    WorkflowRun,
    WorkflowRunHistory,
    WorkflowStep,
)
from finagent.database.seeds import sid  # noqa: E402
from finagent.database.session import session_factory, transaction  # noqa: E402
from finagent.integrations.notifications.port import FileNotificationAdapter  # noqa: E402
from finagent.security.authentication.tokens import LocalDevTokenValidator  # noqa: E402
from finagent.services.case_service.transitions import Actor  # noqa: E402
from finagent.services.workflow_service.runs import resume_run  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        FAILURES.append(label)


def worker(name: str, sink: Path, ticks: int, faults: str | None = None) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "FINAGENT_LEASE_TTL_SECONDS": "5",
        "FINAGENT_NOTIFICATION_BACKEND": "file",
        "FINAGENT_NOTIFICATION_DIR": str(sink),
    }
    if faults:
        env["FINAGENT_FAULTS"] = faults
    return subprocess.Popen(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-m",
            "finagent.worker",
            "--max-ticks",
            str(ticks),
            "--interval",
            "1",
            "--worker-id",
            name,
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def wait_for(predicate: Any, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise SystemExit(f"timed out waiting for: {what}")


def main() -> int:
    settings = get_settings()
    factory = session_factory(settings.database_url.get_secret_value())
    secret = settings.dev_jwt_secret.get_secret_value() if settings.dev_jwt_secret else ""
    issuer = LocalDevTokenValidator(secret, settings.api_audience)

    def bearer(sub: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {issuer.issue(sub)}", "Idempotency-Key": f"demo-{uuid.uuid4()}"}

    sink = ROOT / "var" / f"demo-slice2-{uuid.uuid4().hex[:8]}"
    notifications = FileNotificationAdapter(sink)
    client = TestClient(create_app(settings))
    ref = f"AML-SLICE2-{uuid.uuid4().hex[:8]}"
    alert = {
        "schema_version": "1.0",
        "event_id": f"evt-{uuid.uuid4()}",
        "source_system": "tm-synthetic-a",
        "source_event_time": "2026-10-15T07:00:00Z",
        "payload": {
            "source_alert_id": ref,
            "alert_type": "TM_STRUCTURING",
            "occurred_at": "2026-10-15T06:59:00Z",
            "subject_refs": ["customer-demo-001"],
            "severity": "HIGH",
            "window_start": "2026-09-15T00:00:00Z",
            "window_end": "2026-10-15T00:00:00Z",
        },
    }
    case = client.post("/api/v1/alerts", json=alert, headers=bearer("a.tm-connector@synthetic")).json()
    detail = client.get(f"/api/v1/cases/{case['case_id']}", headers=bearer("a.supervisor1@synthetic")).json()
    analyst1 = str(sid("principal:a:analyst1"))
    analyst = "a.analyst1@synthetic" if detail["assigned_analyst_id"] == analyst1 else "a.analyst2@synthetic"
    start = client.post("/api/v1/workflows", json={"case_id": case["case_id"]}, headers=bearer(analyst))
    run_id = uuid.UUID(start.json()["run_id"])
    print(
        f"1. alert {ref} -> case {case['case_id']} -> run {run_id} "
        f"({start.status_code} {start.json()['status']})"
    )

    def step_status(node: str) -> str | None:
        with transaction(factory) as s:
            return s.scalar(
                select(WorkflowStep.status).where(WorkflowStep.run_id == run_id, WorkflowStep.node == node)
            )

    a = worker("demo-worker-A", sink, ticks=60, faults="after_effect:notify_assignee:sleep=120")
    wait_for(
        lambda: notifications.count() == 1 and step_status("notify_assignee") == "RUNNING", 60, "A sends"
    )
    a.kill()  # hard kill: no lease release, no step commit
    a.wait()
    with transaction(factory) as s:
        lease = s.scalars(select(Lease).where(Lease.resource_id == run_id)).one()
        status = s.scalar(select(WorkflowRun.status).where(WorkflowRun.id == run_id))
    print(
        f"2. worker A killed mid-step: run={status}, lease owner={lease.owner} token={lease.fencing_token}, "
        f"notifications sent={notifications.count()}"
    )

    b = worker("demo-worker-B", sink, ticks=40)
    wait_for(
        lambda: (
            transaction_scalar(factory, select(WorkflowRun.status).where(WorkflowRun.id == run_id))
            == "WAITING_REVIEW"
        ),
        60,
        "B to resume the run",
    )
    print("3. worker B took over after lease expiry and reached WAITING_REVIEW")
    with transaction(factory) as s:
        run = s.get(WorkflowRun, run_id)
        assert run is not None
        resume_run(
            s, run.tenant_id, run_id, {"decision": "CONTINUE"}, Actor.system("demo"), "demo reviewer continue"
        )
    wait_for(
        lambda: (
            transaction_scalar(factory, select(WorkflowRun.status).where(WorkflowRun.id == run_id))
            == "SUCCEEDED"
        ),
        60,
        "run to succeed",
    )
    b.terminate()
    b.wait(timeout=30)
    print("4. resumed by the deterministic resume service -> SUCCEEDED\n\nChecks:")

    with transaction(factory) as s:
        ops = s.scalars(select(ExternalOperation).where(ExternalOperation.run_id == run_id)).all()
        started = s.scalars(
            select(OutboxEvent).where(
                OutboxEvent.resource_id == str(run_id), OutboxEvent.event_type == "workflow.started.v1"
            )
        ).all()
        inbox = s.scalar(
            select(func.count()).select_from(InboxEvent).where(InboxEvent.event_id == started[0].id)
        )
        rows = s.scalars(select(WorkflowStep).where(WorkflowStep.run_id == run_id)).all()
        takeover = s.scalars(
            select(WorkflowRunHistory.reason).where(
                WorkflowRunHistory.run_id == run_id,
                WorkflowRunHistory.from_status == "RUNNING",
                WorkflowRunHistory.to_status == "RUNNING",
            )
        ).all()
    by_node = {r.node: r for r in rows}
    check("exactly one notification delivered (no duplicate external effect)", notifications.count() == 1)
    check(
        "ledger: one operation, CONFIRMED, sent once",
        [(o.status, o.attempts) for o in ops] == [("CONFIRMED", 1)],
    )
    check("workflow.started.v1 emitted once and processed once (inbox)", len(started) == 1 and inbox == 1)
    check(
        "steps before the crash were not repeated",
        by_node["authorize"].attempt == 1 and by_node["record_context"].attempt == 1,
    )
    check(
        "interrupted step resumed by the new worker with a higher fencing token",
        by_node["notify_assignee"].status == "SUCCEEDED" and by_node["notify_assignee"].fencing_token > 1,
    )
    check("takeover recorded in run history", any("demo-worker-B" in r for r in takeover))
    check("every step succeeded exactly once", all(r.status == "SUCCEEDED" for r in rows) and len(rows) == 5)
    print(f"\nSLICE 2: {'PASS' if not FAILURES else 'FAIL'} ({len(FAILURES)} failed checks)")
    return 1 if FAILURES else 0


def transaction_scalar(factory: Any, stmt: Any) -> Any:
    with transaction(factory) as s:
        return s.scalar(stmt)


if __name__ == "__main__":
    raise SystemExit(main())
