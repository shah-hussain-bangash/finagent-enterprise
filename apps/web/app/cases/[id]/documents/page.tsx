import { CasePageHeading } from "@/components/CasePageHeading";
import { randomUUID } from "node:crypto";
import Link from "next/link";
import { UploadDocumentForm } from "@/components/UploadDocumentForm";
import { Empty, ErrorState, Flash, Tabs, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Doc {
  document_id: string; document_type: string; title: string; source: string; current_version_no: number;
  status: string | null; status_reason: string | null; next_action: string | null; created_at: string;
}
const STATUSES = ["RECEIVED", "QUARANTINED", "STORED", "PARSING", "PARSED", "OCR_UNAVAILABLE", "PARSE_FAILED"];
const TYPES = ["PASSPORT", "PROOF_OF_ADDRESS", "BANK_STATEMENT", "SOURCE_OF_FUNDS", "OTHER"];

export default async function Documents({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const validOffset = sp.offset === undefined || /^(0|[1-9][0-9]{0,5}|1000000)$/.test(sp.offset);
  const offset = validOffset && sp.offset ? Number(sp.offset) : 0;
  const status = sp.status && STATUSES.includes(sp.status) ? sp.status : undefined;
  const documentType = sp.document_type && TYPES.includes(sp.document_type) ? sp.document_type : undefined;
  const invalidFilter = Boolean((sp.status && !status) || (sp.document_type && !documentType));
  const pagePath = (position: number) => {
    const params = new URLSearchParams({ ...(status ? {status} : {}), ...(documentType ? {document_type: documentType} : {}), ...(position ? {offset: String(position)} : {}) });
    return `/cases/${id}/documents${params.size ? `?${params}` : ""}`;
  };
  const returnPath = pagePath(offset);
  await requireSession(returnPath);
  const uncertainAction = typeof sp.error === "string" && sp.error.startsWith("CONNECTION_ERROR:");
  const apiParams = new URLSearchParams({limit: "50", offset: String(offset), ...(status ? {status} : {}), ...(documentType ? {document_type: documentType} : {})});
  const result = invalidFilter ? null : await load<{ items: Doc[]; can_upload?: boolean; upload_requires_mfa?: boolean; applied_pagination?: { offset: number; limit: number }; applied_filters?: {status: string | null; document_type: string | null}; next_offset?: number | null }>(`/api/v1/cases/${id}/documents?${apiParams}`, returnPath);
  const data = result?.data ?? null, error = result?.error ?? null;
  const paginationAvailable = data?.applied_pagination?.limit === 50 && data.applied_pagination.offset === offset
    && (data.next_offset === null || data.next_offset === offset + 50)
    && (!status || data.applied_filters?.status === status)
    && (!documentType || data.applied_filters?.document_type === documentType);
  return (
    <section className="case-journey" aria-labelledby="h">
      <CasePageHeading caseId={id} title="Documents" description="Review documents and their processing status for this case." />
      <Tabs caseId={id} active="/documents" />
      <p><Link href={`/workspace/recorded-provenance?${new URLSearchParams({ case: id })}`}>View recorded document provenance →</Link></p>
      <Flash ok={sp.ok} error={sp.error} />
      {!validOffset && <p role="alert">The requested page was invalid. Showing the first page.</p>}
      <form method="get" className="panel case-queue-filter">
        <label htmlFor="document-status">Processing status</label>
        <select id="document-status" name="status" defaultValue={status ?? ""}><option value="">All statuses</option>{STATUSES.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select>
        <label htmlFor="document-type-filter">Document type</label>
        <select id="document-type-filter" name="document_type" defaultValue={documentType ?? ""}><option value="">All types</option>{TYPES.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select>
        <button type="submit">Apply filters</button>
        {(sp.status || sp.document_type || offset > 0) && <Link href={`/cases/${id}/documents`}>Clear filters</Link>}
      </form>
      {invalidFilter && <p role="alert">A requested filter is invalid. Choose a valid option or clear the filters.</p>}
      {uncertainAction && <p className="warn" role="status">Check the recorded documents before another action. <a href={`/cases/${id}/documents`}>Reload documents</a></p>}
      {!uncertainAction && data?.can_upload && (data.upload_requires_mfa
        ? <p className="warn">Additional authentication is required to upload documents. <Link href="/security">Open account security</Link></p>
        : <UploadDocumentForm caseId={id} requestKey={randomUUID()} />)}
      {data && data.can_upload === false && <p className="muted">Document upload is unavailable for your account or the current case state.</p>}
      {data && data.can_upload === undefined && <p className="muted">Document upload is unavailable until the connected service provides the required access information.</p>}
      {error ? <ErrorState error={error} what="Documents" /> : null}
      {data && !paginationAvailable && <p role="alert" className="warn">Document paging is unavailable on the connected service. Update the API before reviewing this list.</p>}
      {data && paginationAvailable && data.items.length === 0 ? <Empty>No documents were returned for these filters on this page.</Empty> : null}
      {data && paginationAvailable && data.items.length > 0 ? (
        <div className="case-documents-table" role="region" aria-label="Case documents table" tabIndex={0}><table>
          <caption>Documents returned for this case</caption>
          <thead><tr><th scope="col">Document</th><th scope="col">Type / source</th><th scope="col">Processing status</th><th scope="col">Created</th></tr></thead>
          <tbody>
            {data.items.map((d) => (
              <tr key={d.document_id}>
                <td><Link href={`/documents/${d.document_id}?${new URLSearchParams({returnTo: returnPath})}`}>{d.title}</Link> (v{d.current_version_no})</td>
                <td>{d.document_type} / {d.source}</td>
                <td><span className={`badge badge-${(d.status ?? "").toLowerCase()}`}>{d.status ?? "Not available"}</span>
                  {d.status_reason ? <div className="muted">{d.status_reason}</div> : null}
                  {d.next_action ? <div className="warn">{d.next_action}</div> : null}</td>
                <td><When iso={d.created_at} /></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : null}
      {data && paginationAvailable && <nav aria-label="Case document pages">
        {offset > 0 && <Link href={pagePath(Math.max(0, offset - 50))}>← Previous documents</Link>}
        {typeof data.next_offset === "number" && data.next_offset <= 1000000 && <> <Link href={pagePath(data.next_offset)}>Next documents →</Link></>}
        {typeof data.next_offset === "number" && data.next_offset > 1000000 && <span> Further pages exceed the current browsing limit.</span>}
      </nav>}
      <p className="muted">Status refreshes when you reload: uploads are scanned, then parsed in an isolated worker.</p>
    </section>
  );
}
