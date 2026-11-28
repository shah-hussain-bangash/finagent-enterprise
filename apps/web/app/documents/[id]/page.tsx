import Link from "next/link";
import { randomUUID } from "node:crypto";
import { CorrectDocumentFieldForm } from "@/components/CorrectDocumentFieldForm";
import { UploadDocumentVersionForm } from "@/components/UploadDocumentVersionForm";
import { Empty, ErrorState, Flash, Json, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { safeCaseReturn } from "@/lib/case-return";

interface FieldRow {
  field_id: string; field_key: string; value: string | null; value_type: string; value_state: string;
  from_current_version?: boolean;
  validation_status: string; currency: string | null; page: number | null; row: number | null; section: string | null;
  confidence_note: string | null; origin: string; correction_reason: string | null; created_at: string | null;
}
interface Version {
  version_id: string; version_no: number; status: string; status_reason: string | null; next_action: string | null;
  declared_media_type: string; size_bytes: number; sha256: string; scan_result: string | null; scan_engine: string | null;
  attempts: number; created_at: string; downloadable: boolean;
  extraction: { parser: string; status: string; page_count: number | null; row_count: number | null; warnings: string[]; segments: unknown[] } | null;
  quarantine: { reason_code: string; detail: string; owner_role: string; status: string } | null;
}
interface Doc {
  document_id: string; case_id: string; document_type: string; title: string; source: string; status: string | null;
  current_version_no: number;
  can_upload_version?: boolean; upload_version_requires_mfa?: boolean;
  can_correct_fields?: boolean; correct_fields_requires_mfa?: boolean;
  versions: Version[]; fields: FieldRow[]; field_history: FieldRow[];
}

function where(f: FieldRow): string {
  return f.page ? `page ${f.page}` : f.row ? `row ${f.row}` : f.section ?? "document";
}

export default async function DocumentReview({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const pageInput = typeof sp.fields_page === "string" ? sp.fields_page : undefined;
  const validPage = pageInput === undefined || /^[1-9][0-9]{0,5}$/.test(pageInput);
  const requestedPage = validPage && pageInput ? Number(pageInput) : 1;
  const candidateBack = safeCaseReturn(sp.returnTo);
  const requestedBack = candidateBack && /^\/cases\/[0-9a-f-]{36}\/documents(?:\?|$)/i.test(candidateBack) ? candidateBack : null;
  const reviewPath = (page: number, back: string | null) => {
    const params = new URLSearchParams({ ...(page > 1 ? {fields_page: String(page)} : {}), ...(back ? {returnTo: back} : {}) });
    return `/documents/${id}${params.size ? `?${params}` : ""}`;
  };
  const returnPath = reviewPath(requestedPage, requestedBack);
  await requireSession(returnPath);
  const conflict = typeof sp.error === "string" && sp.error.startsWith("VERSION_CONFLICT:");
  const uncertainAction = conflict || (typeof sp.error === "string" && sp.error.startsWith("CONNECTION_ERROR:"));
  const { data: d, error } = await load<Doc>(`/api/v1/documents/${id}`, returnPath);
  if (error) return <ErrorState error={error} what="Document" />;
  const expectedList = `/cases/${d.case_id}/documents`;
  const listPath = requestedBack && (requestedBack.toLowerCase() === expectedList.toLowerCase() || requestedBack.toLowerCase().startsWith(`${expectedList.toLowerCase()}?`)) ? requestedBack : expectedList;
  const current = d.versions.find((version) => version.version_no === d.current_version_no);
  const pageSize = 100;
  const totalPages = Math.max(1, Math.ceil(d.fields.length / pageSize));
  const fieldsPage = Math.min(requestedPage, totalPages);
  const pageStart = (fieldsPage - 1) * pageSize;
  const visibleFields = d.fields.slice(pageStart, pageStart + pageSize);
  return (
    <section aria-labelledby="h">
      <h1 id="h">Document review: {d.title}</h1>
      <p><Link href={listPath}>← Back to case documents</Link></p>
      <p><Link href={`/cases/${d.case_id}/evidence`}>Review current case evidence →</Link></p>
      <p><Link href={`/workspace/recorded-provenance?${new URLSearchParams({ case: d.case_id, document: d.document_id })}#document-details`}>View recorded document provenance →</Link></p>
      <Flash ok={sp.ok} error={sp.error} />
      {uncertainAction && <p className="warn" role="status">{conflict ? "The document changed while you were working. Review the latest version before another action." : "Check the recorded document before another action."} <a href={reviewPath(fieldsPage, listPath)}>Reload document</a></p>}
      {!current && <p role="alert" className="warn">The current document version was not returned. Download and correction controls are unavailable until it can be verified.</p>}
      {current?.quarantine ? (
        <p role="alert" className="flash flash-error">Quarantined ({current.quarantine.reason_code}): {current.quarantine.detail}
          {" "}Owner: {current.quarantine.owner_role}. The original file is not available for download while quarantined.</p>
      ) : null}
      {current?.next_action && !current.quarantine ? <p className="warn">{current.next_action}</p> : null}
      {current?.downloadable ? <p><a href={`/documents/${id}/download`}>Download original (short-lived, re-authorized link)</a></p> : null}
      {current && !current.downloadable && !current.quarantine ? <p className="muted">The original file is not currently available for download. Check the processing status and any next action.</p> : null}
      {d.can_upload_version && current && !uncertainAction && (d.upload_version_requires_mfa
        ? <p className="warn">Additional authentication is required to upload a new version. <Link href="/security">Open account security</Link></p>
        : <UploadDocumentVersionForm documentId={id} requestKey={randomUUID()} returnTo={listPath} />)}
      {d.source === "UPLOAD" && d.can_upload_version === false && <p className="muted">A new version is unavailable for your account or the current case state.</p>}
      {d.source === "UPLOAD" && d.can_upload_version === undefined && <p className="muted">New-version upload is unavailable until the connected service provides the required access information.</p>}
      {d.source === "CONNECTOR" && <p className="muted">This document comes from a connected source. New versions must be refreshed from that source.</p>}
      <h2>Extracted fields ({d.document_type})</h2>
      <p className="muted">Values are extracted from the document; authenticity is not verified. Corrections add a new value and keep the original.</p>
      {d.can_correct_fields && d.correct_fields_requires_mfa && <p className="warn">Additional authentication is required to correct extracted fields. <Link href="/security">Open account security</Link></p>}
      {d.can_correct_fields === false && <p className="muted">Field correction is unavailable for your account or the current case state.</p>}
      {d.can_correct_fields === undefined && <p className="muted">Field correction is unavailable until the connected service provides the required access information.</p>}
      {!validPage && <p role="alert">The requested field page was invalid. Showing the first page.</p>}
      {requestedPage > totalPages && <p role="status">The requested page no longer exists. Showing the last available page.</p>}
      {d.fields.length > pageSize && <p role="status">Showing fields {pageStart + 1}–{pageStart + visibleFields.length} of {d.fields.length}, page {fieldsPage} of {totalPages}.</p>}
      {d.fields.length === 0 ? <Empty>No fields extracted.</Empty> : (
        <div className="document-review-table" role="region" aria-label="Extracted document fields table" tabIndex={0}><table>
          <caption>Extracted fields and corrections</caption>
          <thead><tr><th scope="col">Field</th><th scope="col">Value</th><th scope="col">State / validation</th><th scope="col">Provenance</th><th scope="col">Origin</th><th scope="col">Correct</th></tr></thead>
          <tbody>
            {visibleFields.map((f) => (
              <tr key={f.field_id}>
                <td>{f.field_key}</td>
                <td>{f.value ?? <span className="muted">-</span>}{f.currency ? ` ${f.currency}` : ""}</td>
                <td>{f.value_state} / {f.validation_status}<div className="muted">{f.confidence_note}</div></td>
                <td>{where(f)}</td>
                <td>{f.origin}{f.from_current_version === false && <div className="warn">Earlier document version</div>}{f.from_current_version === undefined && <div className="warn">Version association unavailable</div>}{f.correction_reason ? <div className="muted">{f.correction_reason}</div> : null}</td>
                <td>
                  {!uncertainAction && current && d.can_correct_fields && !d.correct_fields_requires_mfa && f.from_current_version === true && <CorrectDocumentFieldForm documentId={id} fieldId={f.field_id} fieldKey={f.field_key} fieldsPage={fieldsPage} returnTo={listPath} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {totalPages > 1 && <nav aria-label="Extracted fields pages">
        {fieldsPage > 1 && <Link href={reviewPath(fieldsPage - 1, listPath)}>← Previous fields</Link>}
        {" "}Page {fieldsPage} of {totalPages}{" "}
        {fieldsPage < totalPages && <Link href={reviewPath(fieldsPage + 1, listPath)}>Next fields →</Link>}
      </nav>}
      {d.field_history.length > 0 ? (
        <>
          <h2>Superseded values (kept)</h2>
          <ul>{d.field_history.map((f) => <li key={f.field_id}>{f.field_key}: {f.value} ({f.origin}, {where(f)})</li>)}</ul>
        </>
      ) : null}
      <h2>Versions</h2>
      {d.versions.length === 0 ? <Empty>No document versions were returned.</Empty> : <div className="document-review-table" role="region" aria-label="Document versions table" tabIndex={0}><table>
        <caption>Recorded document versions</caption>
        <thead><tr><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Scan</th><th scope="col">Parser</th><th scope="col">Size / sha256</th><th scope="col">Created</th></tr></thead>
        <tbody>
          {d.versions.map((v) => (
            <tr key={v.version_id}>
              <td>v{v.version_no}{v.extraction && <div><Link href={`/documents/${id}/versions/${v.version_id}/segments`}>Browse extracted segments</Link></div>}</td>
              <td>{v.status}{v.status_reason ? <div className="muted">{v.status_reason}</div> : null}</td>
              <td>{v.scan_result} <div className="muted">{v.scan_engine}</div></td>
              <td>{v.extraction ? `${v.extraction.parser} (${v.extraction.status})` : "-"}
                {v.extraction?.warnings.length ? <div className="muted">{v.extraction.warnings.join(", ")}</div> : null}</td>
              <td>{v.size_bytes} B <div className="muted">{v.sha256.slice(0, 16)}</div></td>
              <td><When iso={v.created_at} /></td>
            </tr>
          ))}
        </tbody>
      </table></div>}
      {current?.extraction ? (
        <details><summary>Extracted text segments with provenance</summary>
          <p>{current.extraction.segments.length} segments returned. The service returns at most 200 segments; additional segments may exist.</p>
          <p><Link href={`/documents/${id}/versions/${current.version_id}/segments`}>Browse extracted segments in pages →</Link></p>
          {current.extraction.segments.length === 0 ? <Empty>No extracted text segments were returned.</Empty> :
            Array.from({ length: Math.ceil(current.extraction.segments.length / 50) }, (_, index) => {
              const start = index * 50;
              const segments = current.extraction!.segments.slice(start, start + 50);
              return <details key={start}><summary>Segments {start + 1}–{start + segments.length}</summary><Json value={segments} /></details>;
            })}
        </details>
      ) : null}
    </section>
  );
}
