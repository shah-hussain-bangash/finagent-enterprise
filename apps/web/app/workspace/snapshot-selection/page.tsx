import Link from "next/link";
import { Empty, ErrorState, When } from "@/components/ui";
import { api, ApiRequestError } from "@/lib/api";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Snapshot document selection | FinAgent Enterprise" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface Selection { document_type: string; selection: string; evidence_ref: string | null; source_ref: string | null; processing_status: string | null }
interface Output { output_id: string; capability_id: string; snapshot_id: string; status: string; created_at: string; lineage?: { document_selection?: { rule_version: string; review_date: string; basis: string; items: Selection[] } } }
interface SnapshotItem { evidence_item_id: string; source_type: string; source_ref: string; source_system: string; version_no: number; freshness: string; completeness: string; content_hash: string; content: { document_type?: string; status?: string; fields?: { field_key: string; value: string | null; value_state: string; validation_status: string; page?: number | null; origin?: string | null }[] } | null }
interface Snapshot { snapshot_id: string; created_at: string; items: SnapshotItem[] }

export default async function SnapshotSelection({ searchParams }: { searchParams: Promise<{ case?: string; output?: string }> }) {
  await requireSession();
  const params = await searchParams;
  const caseId = typeof params.case === "string" ? params.case : "";
  const outputId = typeof params.output === "string" ? params.output : "";
  const valid = UUID.test(caseId) && UUID.test(outputId);
  let output: Output | null = null;
  let outputError: ApiRequestError | null = null;
  let snapshot: Snapshot | null = null;
  let snapshotError: ApiRequestError | null = null;
  if (valid) {
    try {
      const data = await api<{ items: Output[] }>(`/api/v1/cases/${caseId}/agent-outputs`);
      output = data.items.find((item) => item.output_id === outputId && item.capability_id === "AG-KYC") ?? null;
    } catch (error) { if (!(error instanceof ApiRequestError)) throw error; outputError = error; }
  }
  if (output?.lineage?.document_selection && UUID.test(output.snapshot_id)) {
    try { snapshot = await api<Snapshot>(`/api/v1/cases/${caseId}/snapshots/${output.snapshot_id}`); }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; snapshotError = error; }
  }
  const selection = output?.lineage?.document_selection;
  return <section className="connected-workspace" aria-labelledby="selection-title">
    <p><Link prefetch={false} href={UUID.test(caseId) ? `/workspace/recorded-provenance?${new URLSearchParams({ case: caseId })}` : "/workspace/recorded-provenance"}>← Back to case provenance</Link></p>
    <h1 id="selection-title">Recorded snapshot document selection</h1>
    <div className="panel"><strong>Connected, read-only view</strong><p>This shows the evidence recorded in the KYC output’s snapshot. It may differ from current case evidence. Selection does not establish document authenticity or checklist approval.</p></div>
    {!valid && <p role="alert">A valid case and output are required.</p>}
    {outputError && <ErrorState error={outputError} what="Recorded KYC output" />}
    {valid && !outputError && !output && <Empty>No accessible KYC output was returned for this case.</Empty>}
    {output && <section className="panel"><h2>Recorded KYC output · {output.status}</h2>
      <p>Output: {output.output_id} · Snapshot: {output.snapshot_id} · Recorded <When iso={output.created_at} /></p>
      {!selection ? <Empty>Selection details were not recorded for this output.</Empty> : <>
        <p>Rule: {selection.rule_version} · Review date: {selection.review_date}. {selection.basis}</p>
        {snapshotError && <ErrorState error={snapshotError} what="Evidence snapshot" />}
        {!UUID.test(output.snapshot_id) && <p role="alert">The recorded snapshot identifier is invalid.</p>}
        {snapshot && snapshot.snapshot_id === output.snapshot_id && <><p>Snapshot created <When iso={snapshot.created_at} />.</p>
          {!Array.isArray(selection.items) || selection.items.length === 0 ? <Empty>No document selection items were recorded.</Empty> : selection.items.map((item, index) => {
            const evidence = item.selection === "SELECTED" && typeof item.evidence_ref === "string" ? snapshot!.items.find((source) => `ev:${source.evidence_item_id}` === item.evidence_ref && source.source_type === "DOCUMENT_EXTRACTION") : undefined;
            return <article className="panel" key={index}><h3>{item.document_type} · {item.selection}</h3>
              <p>Recorded evidence: {item.evidence_ref ?? "None"} · Source: {item.source_ref ?? "None"} · Processing: {item.processing_status ?? "Not recorded"}</p>
              {item.selection === "SELECTED" && !evidence && <p role="alert">The selected document evidence was not found in this snapshot.</p>}
              {evidence && <>{!evidence.content ? <p role="alert">The snapshot document content was not returned. The recorded selection cannot be verified here.</p> : (evidence.content.document_type !== item.document_type || evidence.source_ref !== item.source_ref || evidence.content.status !== item.processing_status) && <p role="alert">The recorded selection details do not match this snapshot record. Review the original evidence before relying on the selection.</p>}<p>Snapshot source: {evidence.source_ref} · {evidence.source_system} · version {evidence.version_no} · {evidence.freshness} / {evidence.completeness} · sha256 {evidence.content_hash.slice(0, 12)}</p>
                {evidence.content && <>
                <p>Snapshot document type: {evidence.content.document_type ?? "Not recorded"} · Processing: {evidence.content.status ?? "Not recorded"}</p>
                {!Array.isArray(evidence.content.fields) || evidence.content.fields.length === 0 ? <p>No extracted fields were recorded in this snapshot.</p> : <ul>{evidence.content.fields.map((field, fieldIndex) => <li key={fieldIndex}>{field.field_key}: {field.value ?? "Not recorded"} · {field.value_state} · {field.validation_status}{field.page ? ` · page ${field.page}` : ""}{field.origin ? ` · ${field.origin}` : ""}</li>)}</ul>}
                </>}
              </>}
            </article>;
          })}
        </>}
        {snapshot && snapshot.snapshot_id !== output.snapshot_id && <p role="alert">The returned snapshot does not match the recorded output.</p>}
      </>}
      <p><Link href={`/cases/${caseId}/evidence`} prefetch={false}>Open current case evidence →</Link></p>
    </section>}
  </section>;
}
