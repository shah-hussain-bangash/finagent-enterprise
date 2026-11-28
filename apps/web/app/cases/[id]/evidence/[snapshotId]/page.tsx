import Link from "next/link";
import { CasePageHeading } from "@/components/CasePageHeading";
import { Empty, ErrorState, Json, Tabs, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { DocumentEvidenceLink } from "@/components/DocumentEvidenceLink";

interface SnapshotItem {
  evidence_item_id: string;
  source_type: string;
  source_ref: string;
  source_system: string;
  version_no: number;
  freshness: string;
  completeness: string;
  as_of: string | null;
  content_hash: string;
  retrieved_at: string;
  classification: string;
  content: unknown;
}
interface Gap { code: string; severity: string; source_type: string; source_ref: string | null; detail: string }
interface Snapshot {
  snapshot_id: string;
  version_no: number;
  completeness_status: string;
  open_conflicts: number;
  content_hash: string;
  requirements_version: string;
  gaps: Gap[];
  created_at: string;
  created_by: string;
  items: SnapshotItem[];
}

export default async function SnapshotPage({ params, searchParams }: {
  params: Promise<{ id: string; snapshotId: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id, snapshotId } = await params;
  const query = await searchParams;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!uuid.test(id) || !uuid.test(snapshotId)) {
    return <section><h1>Snapshot unavailable</h1><p>The requested snapshot identifier is invalid.</p></section>;
  }
  const requestedItem = typeof query.item === "string" && uuid.test(query.item) ? query.item.toLowerCase() : null;
  const path = `/cases/${id}/evidence/${snapshotId}${requestedItem ? `?item=${requestedItem}` : ""}`;
  await requireSession(path);
  const { data, error } = await load<Snapshot>(`/api/v1/cases/${id}/snapshots/${snapshotId}`, path);
  return <section className="case-journey" aria-labelledby="h">
    <CasePageHeading caseId={id} title="Evidence snapshot" description="Inspect the source versions captured in this snapshot." />
    <Tabs caseId={id} active="/evidence" />
    <p><Link href={`/cases/${id}/evidence`}>← Back to evidence</Link></p>
    {error && <ErrorState error={error} what="Evidence snapshot" />}
    {data && <>
      <h2>Snapshot version {data.version_no}</h2>
      <p>Recorded <When iso={data.created_at} /> by {data.created_by}. Completeness: {data.completeness_status}; open conflicts: {data.open_conflicts}.</p>
      <p>Requirements version: {data.requirements_version}. Snapshot SHA-256: <code>{data.content_hash}</code></p>
      <p>These are recorded source versions, not a statement that the evidence is complete or current.</p>
      <h3>Recorded gaps</h3>
      {data.gaps.length === 0 ? <Empty>No gaps were recorded in this snapshot.</Empty> :
        <ul>{data.gaps.map((gap, index) => <li key={index}><strong>{gap.severity}</strong>: {gap.code} — {gap.detail}{gap.source_ref && <> ({gap.source_type} {gap.source_ref})</>}</li>)}</ul>}
      <h3>Recorded evidence</h3>
      {query.item && !requestedItem && <p className="warn" role="alert">The requested evidence reference is invalid.</p>}
      {requestedItem && !data.items.some((item) => item.evidence_item_id.toLowerCase() === requestedItem) && <p className="warn" role="alert">The referenced evidence item was not returned in this snapshot. Review the claim and its supporting evidence before making a decision.</p>}
      {requestedItem && data.items.some((item) => item.evidence_item_id.toLowerCase() === requestedItem) && <p role="status">The referenced item is shown in the snapshot below.</p>}
      {data.items.length === 0 ? <Empty>No evidence items were recorded in this snapshot.</Empty> :
        <div className="case-evidence-table" role="region" aria-label="Snapshot evidence table" tabIndex={0}><table>
          <caption>Evidence items recorded in snapshot version {data.version_no}</caption>
          <thead><tr><th scope="col">Source</th><th scope="col">Version</th><th scope="col">Freshness / completeness</th><th scope="col">As of</th><th scope="col">Content</th></tr></thead>
          <tbody>{data.items.map((item) => <tr id={`evidence-${item.evidence_item_id.toLowerCase()}`} key={item.evidence_item_id}>
            <td>{item.evidence_item_id.toLowerCase() === requestedItem && <strong>Referenced item<br /></strong>}{item.source_type}<div className="muted">{item.source_ref} - {item.source_system}</div><div className="muted">Classification: {item.classification}</div></td>
            <td>v{item.version_no}<div className="muted">sha256 {item.content_hash.slice(0, 12)}</div><div className="muted">Retrieved <When iso={item.retrieved_at} /></div></td>
            <td>{item.freshness} / {item.completeness}</td>
            <td><When iso={item.as_of} /></td>
            <td>{item.content === null ? <span>Content is unavailable.</span> : <details open={item.evidence_item_id.toLowerCase() === requestedItem}><summary>View recorded content for {item.source_type} ({item.source_ref})</summary><Json value={item.content} /></details>}<DocumentEvidenceLink sourceType={item.source_type} content={item.content} /></td>
          </tr>)}</tbody>
        </table></div>}
    </>}
  </section>;
}
