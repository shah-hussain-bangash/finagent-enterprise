import Link from "next/link";
import { ConnectedCaseNav } from "@/components/ConnectedCaseNav";
import { Empty, ErrorState, When } from "@/components/ui";
import { api, ApiRequestError, listCases } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { summarizePosted } from "@/lib/transaction-summary";

export const dynamic = "force-dynamic";
export const metadata = { title: "Recorded transactions | FinAgent Enterprise" };
const CASE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ROWS_PER_PAGE = 25;

interface Transaction {
  source_transaction_id?: unknown; booking_time?: unknown; amount?: unknown; currency?: unknown;
  direction?: unknown; status?: unknown; counterparty_ref?: unknown;
}
interface EvidenceItem {
  evidence_item_id: string; source_type: string; source_ref: string; source_system: string;
  version_no: number; freshness: string; completeness: string; as_of: string | null;
  content: { items?: Transaction[] } | null;
}
interface EvidenceGap { source_type: string; detail: string }
const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const hasText = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const isTransactionSource = (value: unknown): value is EvidenceItem => isRecord(value) && value.source_type === "TRANSACTIONS" &&
  hasText(value.evidence_item_id) && hasText(value.source_ref) &&
  hasText(value.source_system) && typeof value.version_no === "number" && Number.isFinite(value.version_no) &&
  hasText(value.freshness) && hasText(value.completeness) &&
  (value.as_of === null || typeof value.as_of === "string");
const isEvidenceGap = (value: unknown): value is EvidenceGap => isRecord(value) && hasText(value.source_type) && hasText(value.detail);
const displayValue = (value: unknown): string => {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return `${value} (unverified format)`;
  return value == null || value === "" ? "Not recorded" : "Invalid value";
};

export default async function RecordedTransactionsPage({ searchParams }: {
  searchParams: Promise<{ case?: string; cursor?: string; q?: string; direction?: string; status?: string; page_source?: string; page?: string }>;
}) {
  const params = await searchParams;
  const returnTo = `/workspace/recorded-transactions?case=${typeof params.case === "string" ? params.case : ""}`;
  await requireSession(returnTo);
  const selected = typeof params.case === "string" ? params.case : "";
  const valid = CASE_ID.test(selected);
  const cursor = typeof params.cursor === "string" ? params.cursor : undefined;
  const query = typeof params.q === "string" ? params.q.trim().slice(0, 100) : "";
  const direction = ["CREDIT", "DEBIT"].includes(params.direction ?? "") ? params.direction! : "";
  const status = ["POSTED", "PENDING", "REVERSED"].includes(params.status ?? "") ? params.status! : "";
  const invalidFilters = Boolean((params.direction && !direction) || (params.status && !status));
  let cases: Awaited<ReturnType<typeof listCases>> | null = null;
  let caseError: ApiRequestError | null = null;
  let evidence: unknown = null;
  let evidenceError: ApiRequestError | null = null;
  try { cases = await listCases(undefined, cursor, returnTo); }
  catch (error) { if (!(error instanceof ApiRequestError)) throw error; caseError = error; }
  if (valid) {
    try { evidence = await api<unknown>(`/api/v1/cases/${selected}/evidence`, { returnTo }); }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; evidenceError = error; }
  }
  const evidenceRecord = isRecord(evidence) ? evidence : null;
  const sourceListAvailable = Array.isArray(evidenceRecord?.items);
  const gapListAvailable = Array.isArray(evidenceRecord?.gaps);
  const sourceItems: unknown[] = sourceListAvailable ? evidenceRecord!.items as unknown[] : [];
  const gapItems: unknown[] = gapListAvailable ? evidenceRecord!.gaps as unknown[] : [];
  const sources = sourceItems.filter(isTransactionSource);
  const malformedSources = sourceItems.filter((item) => !isRecord(item) || !hasText(item.source_type) || (item.source_type === "TRANSACTIONS" && !isTransactionSource(item))).length;
  const gaps = gapItems.filter(isEvidenceGap);
  const malformedGaps = gapItems.length - gaps.length;
  const requestedSource = typeof params.page_source === "string" ? params.page_source : "";
  const requestedPageValue = typeof params.page === "string" ? params.page : "";
  const invalidPagination = Boolean(params.page_source || params.page) && (
    !requestedSource || !/^[1-9]\d{0,6}$/.test(requestedPageValue) ||
    !sources.some((source) => source.evidence_item_id === requestedSource)
  );
  const search = query.toLocaleLowerCase();
  return <section className="connected-workspace" aria-labelledby="recorded-transactions-title">
    <p><Link href="/workspace" prefetch={false}>← Back to workspace preview</Link></p>
    {valid && <p><Link href={`/workspace/case-overview?${new URLSearchParams({ case: selected })}`} prefetch={false}>← Back to case overview</Link></p>}
    <h1 id="recorded-transactions-title">Recorded case transactions</h1>
    {valid && evidence != null && <ConnectedCaseNav caseId={selected} active="transactions" />}
    <div className="panel"><strong>Connected, read-only view</strong>
      <p>Shows current transaction evidence for an accessible case. Records may be incomplete or stale; this is not a live ledger or a risk determination.</p></div>
    <section className="panel"><h2>Choose an accessible case</h2>
      {caseError && <ErrorState error={caseError} what="Cases" />}
      {cases?.items.length === 0 && <Empty>No cases are available on this page.</Empty>}
      {cases && cases.items.length > 0 && <ul>{cases.items.map((item) => <li key={item.case_id}>
        <Link prefetch={false} aria-current={selected === item.case_id ? "true" : undefined} href={`/workspace/recorded-transactions?${new URLSearchParams({ case: item.case_id, ...(cursor ? { cursor } : {}) })}`}>Case {item.case_id.slice(0, 8)}</Link>{selected === item.case_id && <span className="case-selected">Selected</span>} · {item.status}
      </li>)}</ul>}
      {cases?.next_cursor && <p><Link prefetch={false} href={`/workspace/recorded-transactions?${new URLSearchParams({ ...(valid ? { case: selected, ...(query ? { q: query } : {}), ...(direction ? { direction } : {}), ...(status ? { status } : {}), ...(params.page_source ? { page_source: params.page_source } : {}), ...(params.page ? { page: params.page } : {}) } : {}), cursor: cases.next_cursor })}`}>Next cases →</Link></p>}
      {cursor && <p><Link prefetch={false} href={`/workspace/recorded-transactions?${new URLSearchParams(valid ? { case: selected, ...(query ? { q: query } : {}), ...(direction ? { direction } : {}), ...(status ? { status } : {}), ...(params.page_source ? { page_source: params.page_source } : {}), ...(params.page ? { page: params.page } : {}) } : {})}`}>← First cases</Link></p>}
    </section>
    {selected && !valid && <p role="alert">The selected case identifier is invalid.</p>}
    {valid && <section><h2>Transactions for case {selected.slice(0, 8)}</h2>
      {evidenceError && <ErrorState error={evidenceError} what="Transaction evidence" />}
      {!evidenceError && !evidenceRecord && <p role="alert">The transaction evidence response is unavailable or malformed. Review the original case evidence before drawing conclusions.</p>}
      {evidenceRecord && <>
        <form className="panel transaction-filters" method="get"><input type="hidden" name="case" value={selected} />
          {cursor && <input type="hidden" name="cursor" value={cursor} />}
          <label htmlFor="transaction-search">Search transaction ID, counterparty, date, direction or status</label>{" "}
          <input id="transaction-search" name="q" defaultValue={query} maxLength={100} />{" "}
          <label htmlFor="transaction-direction">Direction</label>{" "}<select id="transaction-direction" name="direction" defaultValue={direction}><option value="">All directions</option><option value="CREDIT">Credit</option><option value="DEBIT">Debit</option></select>{" "}
          <label htmlFor="transaction-status">Status</label>{" "}<select id="transaction-status" name="status" defaultValue={status}><option value="">All statuses</option><option value="POSTED">Posted</option><option value="PENDING">Pending</option><option value="REVERSED">Reversed</option></select>{" "}<button type="submit">Apply filters</button>
          {(query || direction || status) && <Link prefetch={false} href={`/workspace/recorded-transactions?${new URLSearchParams({ case: selected, ...(cursor ? { cursor } : {}) })}`}>Clear filters</Link>}
        </form>
        {(query || direction || status) && <p role="status">Active filters: {query ? `search “${query}”` : "all search terms"} · {direction || "all directions"} · {status || "all statuses"}.</p>}
        {invalidFilters && <p role="alert">An unrecognized filter was ignored. Choose a direction and status from the lists.</p>}
        {invalidPagination && <p role="alert">The requested transaction page is invalid or its source is unavailable. Showing the first page for each source.</p>}
        {!sourceListAvailable && <p role="alert">The evidence source list is unavailable or malformed. This does not establish that transaction evidence is absent.</p>}
        {malformedSources > 0 && <p role="alert">{malformedSources} evidence source record(s) could not be read. Review the original case evidence.</p>}
        {!gapListAvailable && <p role="alert">Evidence gap information is unavailable or malformed. Review the original case evidence.</p>}
        {malformedGaps > 0 && <p role="alert">{malformedGaps} evidence gap record(s) could not be read. Review the original case evidence.</p>}
        {gaps.filter((gap) => gap.source_type === "TRANSACTIONS").map((gap, index) => <p role="status" key={index}>Evidence gap: {gap.detail}</p>)}
        {sourceListAvailable && malformedSources === 0 && sources.length === 0 && <Empty>No current transaction evidence is available for this case. This does not establish that no transactions exist.</Empty>}
        {sources.map((source) => {
          const rowsAvailable = Array.isArray(source.content?.items);
          const rawRows: unknown[] = rowsAvailable ? source.content!.items! : [];
          const rows = rawRows.filter((row): row is Transaction => row !== null && typeof row === "object" && !Array.isArray(row));
          const malformedRows = rawRows.length - rows.length;
          const filtered = rows.filter((row) => (!direction || row.direction === direction) && (!status || row.status === status) && (!search || [row.source_transaction_id, row.counterparty_ref, row.booking_time, row.direction, row.status].some((value) => typeof value === "string" && value.toLocaleLowerCase().includes(search)))).slice().sort((a, b) => {
            const first = typeof a.booking_time === "string" ? Date.parse(a.booking_time) : NaN;
            const second = typeof b.booking_time === "string" ? Date.parse(b.booking_time) : NaN;
            if (!Number.isFinite(first)) return Number.isFinite(second) ? 1 : 0;
            if (!Number.isFinite(second)) return -1;
            return first - second;
          });
          const summary = summarizePosted(filtered);
          const requestedPage = params.page_source === source.evidence_item_id && typeof params.page === "string" && /^[1-9]\d{0,6}$/.test(params.page) ? Number(params.page) : 1;
          const pageCount = Math.max(1, Math.ceil(filtered.length / ROWS_PER_PAGE));
          const currentPage = Math.min(requestedPage, pageCount);
          const pageOutOfRange = requestedPage > pageCount;
          const firstRow = (currentPage - 1) * ROWS_PER_PAGE;
          const visibleRows = filtered.slice(firstRow, firstRow + ROWS_PER_PAGE);
          const pageLink = (page: number) => `/workspace/recorded-transactions?${new URLSearchParams({ case: selected, ...(cursor ? { cursor } : {}), ...(query ? { q: query } : {}), ...(direction ? { direction } : {}), ...(status ? { status } : {}), page_source: source.evidence_item_id, page: String(page) })}`;
          return <article className="panel" key={source.evidence_item_id}>
            <h3>Evidence source: {source.source_ref}</h3>
            <p>{source.source_system} · Version {source.version_no} · {source.freshness} / {source.completeness} · As of <When iso={source.as_of} /></p>
            {!rowsAvailable ? <p role="alert">Transaction rows are unavailable or malformed for this evidence source. This does not establish that there are no transactions.</p> : <>
            {malformedRows > 0 && <p role="alert">{malformedRows} malformed transaction record(s) could not be displayed or included in summaries. Review the source evidence.</p>}
            {pageOutOfRange && <p role="status">The requested transaction page is unavailable. Showing page {currentPage} of {pageCount} for this source.</p>}
            <p>Ordered by booking time, oldest first. Unavailable dates appear last. Each evidence source is shown separately.</p>
            <h4>Filtered posted amounts by currency and direction</h4>
            <p>Calculated from readable filtered rows in this evidence source, including rows on other pages; not a balance or a risk assessment. Evidence may be incomplete.</p>
            {summary.totals.length ? <ul>{summary.totals.map((total) => <li key={`${total.currency}-${total.direction}`}>{total.currency} · {total.direction}: {total.amount} ({total.count} posted transaction(s))</li>)}</ul> : <p>No summarizable posted amounts match the filters.</p>}
            <p>Pending or reversed rows excluded: {summary.excluded}. Rows not summarized due to unrecognized status, currency, direction or amount: {summary.unrecognized}.</p>
            {filtered.length === 0 ? <Empty>{rows.length ? "No recorded transactions match the selected filters." : malformedRows ? "No readable transaction records are available." : "This evidence source contains no transaction rows."}</Empty> :
              <div style={{ overflowX: "auto" }} role="region" aria-label={`Transactions for evidence source ${source.source_ref}`} tabIndex={0}><table><caption>Transactions from this evidence source</caption><thead><tr><th scope="col">Transaction</th><th scope="col">Booked</th><th scope="col">Counterparty</th><th scope="col">Direction</th><th scope="col">Amount</th><th scope="col">Status</th></tr></thead>
                <tbody>{visibleRows.map((row, index) => <tr key={firstRow + index}><td>{displayValue(row.source_transaction_id)}</td><td>{displayValue(row.booking_time)}</td><td>{displayValue(row.counterparty_ref)}</td><td>{displayValue(row.direction)}</td><td>{displayValue(row.currency)} {displayValue(row.amount)}</td><td>{displayValue(row.status)}</td></tr>)}</tbody></table></div>}
            {filtered.length > 0 && <nav className="transaction-pagination" aria-label={`Transaction pages for ${source.source_ref}`}>
              <span>Showing {firstRow + 1}–{firstRow + visibleRows.length} of {filtered.length} filtered transactions · Page {currentPage} of {pageCount}</span>
              {currentPage > 1 && <Link prefetch={false} href={pageLink(currentPage - 1)}>← Previous page</Link>}
              {currentPage < pageCount && <Link prefetch={false} href={pageLink(currentPage + 1)}>Next page →</Link>}
            </nav>}
            </>}
          </article>;
        })}
        <p><Link href={`/cases/${selected}/evidence`} prefetch={false}>Open complete case evidence →</Link></p>
      </>}
    </section>}
  </section>;
}
