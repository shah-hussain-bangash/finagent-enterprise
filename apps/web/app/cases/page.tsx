import Link from "next/link";
import { StatusBadge } from "@/components/StatusBadge";
import { Empty, ErrorState, When } from "@/components/ui";
import { ApiRequestError, type CaseStatus, listCases } from "@/lib/api";

const STATUSES: CaseStatus[] = ["RECEIVED", "ASSIGNED", "INVESTIGATING", "BLOCKED", "AWAITING_REVIEW", "REWORK",
  "APPROVED", "FINALIZING", "COMPLETED", "CANCELLED"];

export default async function Cases({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const status = STATUSES.includes(sp.status as CaseStatus) ? (sp.status as CaseStatus) : undefined;
  let page: Awaited<ReturnType<typeof listCases>> | null = null;
  let error: ApiRequestError | null = null;
  try {
    page = await listCases(status, sp.cursor);
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    error = e;
  }
  return (
    <section aria-labelledby="h">
      <h1 id="h">Case queue</h1>
      <form className="inline" method="get">
        <label htmlFor="status">Status</label>
        <select id="status" name="status" defaultValue={status ?? ""}>
          <option value="">All</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="submit">Filter</button>
      </form>
      {error ? <ErrorState error={error} what="Cases" /> : null}
      {page && page.items.length === 0 ? <Empty>No cases in your queue for this filter.</Empty> : null}
      {page && page.items.length > 0 ? (
        <table>
          <caption>Cases you are permitted to see (newest first)</caption>
          <thead>
            <tr><th>Case</th><th>Status</th><th>Priority</th><th>Alert</th><th>Created</th><th>Updated</th></tr>
          </thead>
          <tbody>
            {page.items.map((c) => (
              <tr key={c.case_id}>
                <td><Link href={`/cases/${c.case_id}`}>{c.case_id.slice(0, 8)}</Link></td>
                <td><StatusBadge status={c.status} /></td>
                <td>{c.priority}</td>
                <td>{c.alert_type} - {c.source_alert_id}</td>
                <td><When iso={c.created_at} /></td>
                <td><When iso={c.updated_at} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {page?.next_cursor ? (
        <p><Link href={`/cases?${new URLSearchParams({ ...(status ? { status } : {}), cursor: page.next_cursor })}`}>Next page</Link></p>
      ) : null}
    </section>
  );
}
