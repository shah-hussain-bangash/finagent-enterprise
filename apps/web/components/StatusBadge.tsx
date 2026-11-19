// Status and finding-type labels carry TEXT, not colour alone (UI-001, NQ-040/041).
import type { CaseStatus } from "@/lib/api";

export const CASE_STATUS_LABEL: Record<CaseStatus, string> = {
  RECEIVED: "Received", ASSIGNED: "Assigned", INVESTIGATING: "Investigating", BLOCKED: "Blocked",
  AWAITING_REVIEW: "Awaiting review", REWORK: "Rework", APPROVED: "Approved",
  FINALIZING: "Finalizing - external update pending", COMPLETED: "Completed", CANCELLED: "Cancelled",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge badge-${status.toLowerCase()}`} aria-label={`Case status: ${CASE_STATUS_LABEL[status]}`}>{CASE_STATUS_LABEL[status]}</span>;
}

export type FindingKind = "FACT" | "DETERMINISTIC_RESULT" | "INTERPRETATION" | "GAP";
const FINDING: Record<FindingKind, string> = {
  FACT: "Fact (sourced)", DETERMINISTIC_RESULT: "Deterministic result",
  INTERPRETATION: "AI interpretation", GAP: "Evidence gap",
};

export function FindingTag({ kind }: { kind: FindingKind }) {
  return <span className={`finding finding-${kind.toLowerCase()}`}>{FINDING[kind]}</span>;
}
