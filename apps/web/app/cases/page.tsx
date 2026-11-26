import Link from "next/link";
import { CASE_STATUS_LABEL, StatusBadge } from "@/components/StatusBadge";
import { Empty, ErrorState, When } from "@/components/ui";
import { ApiRequestError, type CaseStatus, listCases } from "@/lib/api";

const STATUSES: CaseStatus[] = ["RECEIVED", "ASSIGNED", "INVESTIGATING", "BLOCKED", "AWAITING_REVIEW", "REWORK",
  "APPROVED", "FINALIZING", "COMPLETED", "CANCELLED"];
const PRIORITIES = ["LOW", "NORMAL", "HIGH", "URGENT"];

export default async function Cases({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const status = STATUSES.includes(sp.status as CaseStatus) ? (sp.status as CaseStatus) : undefined;
  const priority = sp.priority && PRIORITIES.includes(sp.priority) ? sp.priority : undefined;
  let page: Awaited<ReturnType<typeof listCases>> | null = null;
  let error: ApiRequestError | null = null;
  let priorityUnavailable = false;
  try {
    page = await listCases(status, sp.cursor, undefined, priority);
    if (priority && page.applied_filters?.priority !== priority) {
      priorityUnavailable = true;
      page = null;
    }
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    error = e;
  }
  return (
    <section aria-labelledby="h">
      <div className="portal-page-heading"><p className="portal-eyebrow">INVESTIGATION / WORKLOAD</p>
        <h1 id="h">Case queue</h1>
        <p>Review cases available to your account. Open a case to inspect its investigation and evidence.</p>
      </div>
      <form className="panel case-queue-filter" method="get">
        <label htmlFor="status">Status</label>
        <select id="status" name="status" defaultValue={status ?? ""}>
          <option value="">All</option>
          {STATUSES.map((s) => <option key={s} value={s}>{CASE_STATUS_LABEL[s]}</option>)}
        </select>
        <label htmlFor="priority">Priority</label>
        <select id="priority" name="priority" defaultValue={priority ?? ""}>
          <option value="">All priorities</option>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p.charAt(0) + p.slice(1).toLowerCase()}</option>)}
        </select>
        <button type="submit">Filter</button>
        {(status || priority) && <Link href="/cases">Clear filters</Link>}
      </form>
      {sp.status && !status && <p role="alert">The requested status was not recognized. All statuses are shown.</p>}
      {sp.priority && !priority && <p role="alert">The requested priority was not recognized. All priorities are shown.</p>}
      {priorityUnavailable && <p className="warn" role="alert">Priority filtering is unavailable on the connected service. Clear the filter to view the case queue.</p>}
      {error ? <ErrorState error={error} what="Cases" /> : null}
      {page && page.items.length === 0 ? <Empty>{sp.cursor ? "No cases were returned on this page. Return to the first page to check the queue." : "No cases were returned for this filter."}</Empty> : null}
      {page && page.items.length > 0 ? (
        <div className="case-queue-table" role="region" aria-label="Case queue table" tabIndex={0}><table>
          <caption>Cases you are permitted to see (newest first)</caption>
          <thead>
            <tr><th scope="col">Case</th><th scope="col">Status</th><th scope="col">Priority</th><th scope="col">Alert</th><th scope="col">Created</th><th scope="col">Updated</th></tr>
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
        </table></div>
      ) : null}
      {page?.next_cursor ? (
        <p><Link href={`/cases?${new URLSearchParams({ ...(status ? { status } : {}), ...(priority ? { priority } : {}), cursor: page.next_cursor })}`}>Next page</Link></p>
      ) : null}
      {sp.cursor && <p><Link href={`/cases${status || priority ? `?${new URLSearchParams({ ...(status ? { status } : {}), ...(priority ? { priority } : {}) })}` : ""}`}>← First page</Link></p>}
    </section>
  );
}
