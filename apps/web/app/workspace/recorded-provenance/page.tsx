import Link from "next/link";
import { ConnectedCaseNav } from "@/components/ConnectedCaseNav";
import { Empty, ErrorState, When } from "@/components/ui";
import { api, ApiRequestError, listCases } from "@/lib/api";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Recorded document provenance | FinAgent Enterprise" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface DocumentSummary { document_id: string; title: string; document_type: string; current_version_no: number; status: string | null; status_reason: string | null }
interface Field { field_id: string; field_key: string; value: string | null; origin: string; page: number | null; row: number | null; section: string | null; correction_reason: string | null; validation_status: string; from_current_version?: boolean }
interface Version { version_id: string; version_no: number; status: string; status_reason: string | null; created_at: string; sha256: string; downloadable: boolean }
interface DocumentDetail { document_id: string; case_id: string; title: string; current_version_no: number; versions: Version[]; fields: Field[]; field_history: Field[] }
interface KycOutput { output_id: string; capability_id: string; status: string; snapshot_id: string; created_at: string; lineage?: { document_selection?: { rule_version: string; review_date: string; basis: string; items: { document_type: string; selection: string; evidence_ref: string | null; source_ref: string | null; processing_status: string | null }[] } }; findings: { claim_id: string; claim: string; claim_type: string; support_status: string; evidence_refs: string[] }[] }

export default async function RecordedProvenance({ searchParams }: { searchParams: Promise<{ case?: string; document?: string; cursor?: string; doc_offset?: string }> }) {
  const params = await searchParams;
  const value = typeof params.document === "string" ? params.document : "";
  const returnSelection = UUID.test(value) ? `&document=${value}` : "";
  const validDocOffset = params.doc_offset === undefined || /^(0|[1-9][0-9]{0,5}|1000000)$/.test(params.doc_offset);
  const docOffset = validDocOffset && params.doc_offset ? Number(params.doc_offset) : 0;
  const returnTo = `/workspace/recorded-provenance?case=${typeof params.case === "string" ? params.case : ""}${returnSelection}${docOffset ? `&doc_offset=${docOffset}` : ""}`;
  await requireSession(returnTo);
  const selected = typeof params.case === "string" ? params.case : "";
  const document = typeof params.document === "string" ? params.document : "";
  const cursor = typeof params.cursor === "string" ? params.cursor : undefined;
  const valid = UUID.test(selected);
  let cases: Awaited<ReturnType<typeof listCases>> | null = null;
  let caseError: ApiRequestError | null = null;
  let documents: DocumentSummary[] | null = null;
  let documentsError: ApiRequestError | null = null;
  let documentPageUnavailable = false;
  let nextDocumentOffset: number | null = null;
  let kycOutputs: KycOutput[] | null = null;
  let kycError: ApiRequestError | null = null;
  let detail: DocumentDetail | null = null;
  let detailError: ApiRequestError | null = null;
  try { cases = await listCases(undefined, cursor, returnTo); }
  catch (error) { if (!(error instanceof ApiRequestError)) throw error; caseError = error; }
  if (valid) {
    try {
      const page = await api<{items: DocumentSummary[]; applied_pagination?: {offset: number; limit: number}; next_offset?: number | null}>(`/api/v1/cases/${selected}/documents?limit=50&offset=${docOffset}`, { returnTo });
      if (!Array.isArray(page.items) || page.items.length > 50 || page.applied_pagination?.offset !== docOffset || page.applied_pagination.limit !== 50 || (page.next_offset !== null && page.next_offset !== docOffset + 50)) documentPageUnavailable = true;
      else { documents = page.items; nextDocumentOffset = page.next_offset ?? null; }
    }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; documentsError = error; }
    if (documents) {
      try { kycOutputs = (await api<{ items: KycOutput[] }>(`/api/v1/cases/${selected}/agent-outputs`, { returnTo })).items.filter((item) => item.capability_id === "AG-KYC"); }
      catch (error) { if (!(error instanceof ApiRequestError)) throw error; kycError = error; }
    }
  }
  // The document endpoint rechecks access; render only if it belongs to the selected case.
  if (UUID.test(document) && documents) {
    try { const candidate = await api<DocumentDetail>(`/api/v1/documents/${document}`, { returnTo }); if (candidate.case_id.toLowerCase() === selected.toLowerCase()) detail = candidate; }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; detailError = error; }
  }
  const link = (value: Record<string, string>) => `/workspace/recorded-provenance?${new URLSearchParams(value)}`;
  return <section className="connected-workspace" aria-labelledby="provenance-title">
    <p><Link href="/workspace" prefetch={false}>← Back to workspace preview</Link></p>
    {valid && <p><Link href={`/workspace/case-overview?${new URLSearchParams({ case: selected })}`} prefetch={false}>← Back to case overview</Link></p>}
    <h1 id="provenance-title">Recorded document provenance</h1>
    {documents && <ConnectedCaseNav caseId={selected} active="documents" />}
    <div className="panel"><strong>Connected, read-only view</strong><p>Review recorded versions and field history for an accessible case. Extracted values require human verification. This view does not change documents or determine authenticity.</p></div>
    <section className="panel"><h2>Choose an accessible case</h2>
      {caseError && <ErrorState error={caseError} what="Cases" />}
      {cases?.items.length === 0 && <Empty>No cases are available on this page.</Empty>}
      {cases && cases.items.length > 0 && <ul>{cases.items.map((item) => <li key={item.case_id}><Link prefetch={false} aria-current={selected === item.case_id ? "true" : undefined} href={link({ case: item.case_id, ...(cursor ? { cursor } : {}) })}>Case {item.case_id.slice(0, 8)}</Link>{selected === item.case_id && <span className="case-selected">Selected</span>} · {item.status}</li>)}</ul>}
      {cases?.next_cursor && <p><Link href={link({ ...(valid ? { case: selected, ...(UUID.test(document) ? { document } : {}) } : {}), cursor: cases.next_cursor })} prefetch={false}>Next cases →</Link></p>}
      {cursor && <p><Link href={link(valid ? { case: selected, ...(UUID.test(document) ? { document } : {}) } : {})} prefetch={false}>← First cases</Link></p>}
    </section>
    {selected && !valid && <p role="alert">The selected case identifier is invalid.</p>}
    {valid && <section className="panel"><h2>Case documents</h2>
      {documentsError && <ErrorState error={documentsError} what="Case documents" />}
      {!validDocOffset && <p role="alert">The requested document page was invalid. Showing the first page.</p>}
      {documentPageUnavailable && <p role="alert">Document paging is unavailable on the connected service. Update the API to use this view.</p>}
      {documents?.length === 0 && <Empty>No documents were returned for this case.</Empty>}
      {documents && documents.length > 0 && <ul>{documents.map((item) => <li key={item.document_id}><Link prefetch={false} href={`${link({ case: selected, document: item.document_id, ...(docOffset ? { doc_offset: String(docOffset) } : {}), ...(cursor ? { cursor } : {}) })}#document-details`} aria-current={document === item.document_id ? "true" : undefined}>{item.title}</Link> · {item.document_type} · v{item.current_version_no} · {item.status ?? "Unknown"}{item.status_reason ? ` · ${item.status_reason}` : ""}</li>)}</ul>}
      {documents && <nav aria-label="Case document pages">
        {docOffset > 0 && <Link href={link({ case: selected, doc_offset: String(Math.max(0, docOffset - 50)) })}>← Previous documents</Link>}
        {nextDocumentOffset !== null && nextDocumentOffset <= 1000000 && <> <Link href={link({ case: selected, doc_offset: String(nextDocumentOffset) })}>Next documents →</Link></>}
        {nextDocumentOffset !== null && nextDocumentOffset > 1000000 && <span> Further pages exceed the current browsing limit.</span>}
      </nav>}
      {document && documents && (!UUID.test(document) || (!detail && !detailError)) && <p role="alert">This document is unavailable for the selected case.</p>}
      {detailError && <ErrorState error={detailError} what="Document details" />}
    </section>}
    {detail && detail.case_id.toLowerCase() === selected.toLowerCase() && <section className="panel" id="document-details"><h2>{detail.title}</h2>
      <p>Current document version: {detail.current_version_no}. Version history includes earlier processing failures; it does not by itself describe current case gaps.</p>
      <h3>Recorded versions</h3>
      {detail.versions.length === 0 ? <Empty>No versions were returned.</Empty> : <ul>{detail.versions.map((v) => <li key={v.version_id}>Version {v.version_no} · {v.status}{v.status_reason ? ` · ${v.status_reason}` : ""} · <When iso={v.created_at} /> · sha256 {v.sha256.slice(0, 12)}</li>)}</ul>}
      <h3>Recorded extracted fields</h3>
      {detail.fields.some((f) => f.from_current_version !== true) && <p role="alert" className="warn">Some returned fields are from an earlier version or have an unknown version association. Do not treat them as fields from the current version.</p>}
      {detail.fields.length === 0 ? <Empty>No current fields were returned.</Empty> : <ul>{detail.fields.map((f) => <li key={f.field_id}><strong>{f.field_key}</strong>: {f.value ?? "Not recorded"} · {f.origin} · {f.validation_status} · {f.from_current_version === true ? "Current version" : f.from_current_version === false ? "Earlier version" : "Version association unavailable"} · {f.page ? `page ${f.page}` : f.row ? `row ${f.row}` : f.section ?? "document"}{f.correction_reason ? ` · Correction: ${f.correction_reason}` : ""}</li>)}</ul>}
      <h3>Previous field values</h3>
      {detail.field_history.length === 0 ? <Empty>No previous field values were returned.</Empty> : <ul>{detail.field_history.map((f) => <li key={f.field_id}>{f.field_key}: {f.value ?? "Not recorded"} · {f.origin}</li>)}</ul>}
      <p><Link href={`/documents/${detail.document_id}`} prefetch={false}>Open document review and authorized download →</Link></p>
      <p><Link href={`/cases/${selected}/evidence`} prefetch={false}>Open current case gaps and evidence →</Link></p>
    </section>}
    {documents && <section className="panel"><h2>Recorded KYC checklist findings</h2>
      <p>These findings and references belong to their recorded snapshots; they may not describe current documents. A reference alone does not establish which document was selected or verify its authenticity.</p>
      {kycError && <ErrorState error={kycError} what="Recorded KYC findings" />}
      {kycOutputs?.length === 0 && <Empty>No recorded KYC outputs were returned.</Empty>}
      {kycOutputs && kycOutputs.length > 0 && <p>Showing {kycOutputs.length} recorded KYC output(s), newest first. Open an earlier output to review it.</p>}
      {kycOutputs?.slice().sort((a, b) => {
        const first = Date.parse(a.created_at), second = Date.parse(b.created_at);
        if (!Number.isFinite(first)) return Number.isFinite(second) ? 1 : 0;
        if (!Number.isFinite(second)) return -1;
        return second - first;
      }).map((output, index) => <details className="kyc-history-item" key={output.output_id} open={index === 0}><summary>KYC output · {output.status} · <When iso={output.created_at} /></summary><article>
        <p>Recorded <When iso={output.created_at} /> · Evidence snapshot: {output.snapshot_id}</p>
        {output.lineage?.document_selection && <p><Link prefetch={false} href={`/workspace/snapshot-selection?${new URLSearchParams({ case: selected, output: output.output_id })}`}>Inspect this recorded selection in its snapshot →</Link></p>}
        <h4>Document selection at this snapshot</h4>
        {output.lineage?.document_selection && Array.isArray(output.lineage.document_selection.items) ? <><p>Rule: {output.lineage.document_selection.rule_version} · Review date: {output.lineage.document_selection.review_date}. {output.lineage.document_selection.basis} Selection does not verify authenticity or mean the checklist passed.</p><ul>{output.lineage.document_selection.items.map((item, index) => <li key={index}><strong>{item.document_type}</strong> · {item.selection} · Evidence: {item.evidence_ref ?? "None recorded"} · Source: {item.source_ref ?? "None recorded"} · Processing: {item.processing_status ?? "Not recorded"}</li>)}</ul></> : <p>Selection details were not recorded for this output.</p>}
        {!Array.isArray(output.findings) || output.findings.length === 0 ? <Empty>No findings were returned for this output.</Empty> : <ul>{output.findings.map((finding, index) => <li key={`${finding.claim_id}-${index}`}><strong>{finding.claim_type} · {finding.support_status}</strong>: {finding.claim}<br /><small>Evidence references: {Array.isArray(finding.evidence_refs) && finding.evidence_refs.length ? finding.evidence_refs.join(", ") : "None recorded"}</small></li>)}</ul>}
      </article></details>)}
      <p><Link href={`/cases/${selected}/agents`} prefetch={false}>Open complete recorded agent findings →</Link></p>
    </section>}

  </section>;
}
