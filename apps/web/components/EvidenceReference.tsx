import Link from "next/link";

const EVIDENCE_REF = /^ev:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;
const POLICY_REF = /^cite:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

/** Evidence links open the bound snapshot; policy links are authorized when opened. */
export function EvidenceReference({ reference, caseId, snapshotId }: {
  reference: string; caseId: string; snapshotId: string;
}) {
  const match = EVIDENCE_REF.exec(reference);
  if (match) {
    const selected = match[1]!.toLowerCase();
    return <Link href={`/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(snapshotId)}?item=${selected}#evidence-${selected}`}>{reference}</Link>;
  }
  const policy = POLICY_REF.exec(reference);
  if (policy) return <Link href={`/policies/chunks/${policy[1]!.toLowerCase()}`}>{reference}</Link>;
  return <>{reference}</>;
}
