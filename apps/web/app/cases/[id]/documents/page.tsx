import { randomUUID } from "node:crypto";
import Link from "next/link";
import { uploadDocument } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { Empty, ErrorState, Flash, Tabs, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Doc {
  document_id: string; document_type: string; title: string; source: string; current_version_no: number;
  status: string | null; status_reason: string | null; next_action: string | null; created_at: string;
}

export default async function Documents({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const { id } = await params;
  const sp = await searchParams;
  const { data, error } = await load<{ items: Doc[] }>(`/api/v1/cases/${id}/documents`);
  return (
    <section aria-labelledby="h">
      <h1 id="h">Documents</h1>
      <Tabs caseId={id} active="/documents" />
      <Flash ok={sp.ok} error={sp.error} />
      <form action={uploadDocument} className="stack panel">
        <input type="hidden" name="case_id" value={id} />
        <input type="hidden" name="key" value={randomUUID()} />
        <label htmlFor="file">File (PDF, DOCX or CSV; up to 20 MiB; scanned before use)</label>
        <input id="file" name="file" type="file" accept=".pdf,.docx,.csv" required />
        <label htmlFor="document_type">Document type</label>
        <select id="document_type" name="document_type">
          <option>PASSPORT</option><option>PROOF_OF_ADDRESS</option><option>BANK_STATEMENT</option>
          <option>SOURCE_OF_FUNDS</option><option>OTHER</option>
        </select>
        <SubmitButton>Upload</SubmitButton>
      </form>
      {error ? <ErrorState error={error} what="Documents" /> : null}
      {data && data.items.length === 0 ? <Empty>No documents for this case.</Empty> : null}
      {data && data.items.length > 0 ? (
        <table>
          <thead><tr><th>Document</th><th>Type / source</th><th>Processing status</th><th>Created</th></tr></thead>
          <tbody>
            {data.items.map((d) => (
              <tr key={d.document_id}>
                <td><Link href={`/documents/${d.document_id}`}>{d.title}</Link> (v{d.current_version_no})</td>
                <td>{d.document_type} / {d.source}</td>
                <td><span className={`badge badge-${(d.status ?? "").toLowerCase()}`}>{d.status}</span>
                  {d.status_reason ? <div className="muted">{d.status_reason}</div> : null}
                  {d.next_action ? <div className="warn">{d.next_action}</div> : null}</td>
                <td><When iso={d.created_at} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="muted">Status refreshes when you reload: uploads are scanned, then parsed in an isolated worker.</p>
    </section>
  );
}
