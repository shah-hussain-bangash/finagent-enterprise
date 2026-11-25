import Link from "next/link";
import { Empty, ErrorState, When } from "@/components/ui";
import { ApiRequestError, listCases, load } from "@/lib/api";
import { recordedIndicators, type RecordedOutput } from "@/lib/connected-risk";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Case investigation overview | FinAgent Enterprise" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface CaseDetail { case_id: string; status: string; priority: string; alert_type: string; due_at: string | null; assigned_analyst_id: string | null; blocked_reason: string | null }
interface Evidence { items: { evidence_item_id: string; source_type: string; freshness: string; completeness: string }[]; gaps: { code: string; severity: string; detail: string; source_type: string; source_ref: string | null }[] }

export default async function CaseOverview({ searchParams }: { searchParams: Promise<{ case?: string; cursor?: string }> }) {
  const params = await searchParams;
  await requireSession(`/workspace/case-overview?case=${typeof params.case === "string" ? params.case : ""}`);
  const selected = typeof params.case === "string" ? params.case : "";
  const cursor = typeof params.cursor === "string" ? params.cursor : undefined;
  const valid = UUID.test(selected);
  let cases: Awaited<ReturnType<typeof listCases>> | null = null;
  let caseError: ApiRequestError | null = null;
  try { cases = await listCases(undefined, cursor); }
  catch (error) { if (!(error instanceof ApiRequestError)) throw error; caseError = error; }
  const detail = valid ? await load<CaseDetail>(`/api/v1/cases/${selected}`) : null;
  // Follow-up reads use the same authenticated API and occur only after the case read succeeds.
  const [evidence, outputs] = detail?.data ? await Promise.all([
    load<Evidence>(`/api/v1/cases/${selected}/evidence`),
    load<{ items: RecordedOutput[] }>(`/api/v1/cases/${selected}/agent-outputs`),
  ]) : [null, null];
  const link = (path: string) => `${path}?${new URLSearchParams({ case: selected })}`;
  const indicators = (outputs?.data?.items ?? []).flatMap((output) => recordedIndicators(output).map((finding) => ({ output, finding })));
  return <section className="connected-workspace investigation-overview" aria-labelledby="case-overview-title">
    <p><Link href="/workspace" prefetch={false}>← Back to workspace preview</Link></p>
    <h1 id="case-overview-title">Case investigation overview</h1>
    <div className="panel investigation-notice"><strong>Connected, read-only view</strong><p>Case details, current evidence and previously recorded indicators may refer to different points in time. Review the original records before making a decision.</p></div>
    <section className="panel"><h2>Choose an accessible case</h2>
      {caseError && <ErrorState error={caseError} what="Cases" />}
      {cases?.items.length === 0 && <Empty>No cases are available on this page.</Empty>}
      {cases && cases.items.length > 0 && <ul>{cases.items.map((item) => <li key={item.case_id}><Link prefetch={false} aria-current={selected === item.case_id ? "true" : undefined} href={`/workspace/case-overview?${new URLSearchParams({ case: item.case_id, ...(cursor ? { cursor } : {}) })}`}>Case {item.case_id.slice(0, 8)}</Link>{selected === item.case_id && <span className="case-selected">Selected</span>} · {item.status}</li>)}</ul>}
      {cases?.next_cursor && <p><Link prefetch={false} href={`/workspace/case-overview?${new URLSearchParams({ ...(valid ? { case: selected } : {}), cursor: cases.next_cursor })}`}>Next cases →</Link></p>}
      {cursor && <p><Link prefetch={false} href={valid ? `/workspace/case-overview?${new URLSearchParams({ case: selected })}` : "/workspace/case-overview"}>← First cases</Link></p>}
    </section>
    {selected && !valid && <p role="alert">The selected case identifier is invalid.</p>}
    {detail?.error && <ErrorState error={detail.error} what="Case" />}
    {detail?.data && <>
      <section className="panel investigation-case"><h2>Case {detail.data.case_id.slice(0, 8)}</h2>
        <dl className="investigation-details">
          <div><dt>Status</dt><dd>{detail.data.status}</dd></div>
          <div><dt>Priority</dt><dd>{detail.data.priority}</dd></div>
          <div><dt>Alert</dt><dd>{detail.data.alert_type}</dd></div>
          <div><dt>Due</dt><dd>{detail.data.due_at ? <When iso={detail.data.due_at} /> : "Not recorded"}</dd></div>
          <div><dt>Assigned analyst ID</dt><dd>{detail.data.assigned_analyst_id ?? "Not assigned"}</dd></div>
        </dl>
        {detail.data.blocked_reason && <p>Recorded blocker: {detail.data.blocked_reason}</p>}
      </section>
      <section className="panel"><h2>Current evidence and gaps</h2>
        {evidence?.error && <ErrorState error={evidence.error} what="Evidence" />}
        {evidence?.data && <><p>{evidence.data.items.length} current evidence source(s) · {evidence.data.gaps.length} checklist gap(s)</p>
          {evidence.data.gaps.length > 0 ? <><p>Checklist gaps are reported per source or document record. A document processing warning does not by itself identify the document selected for investigation.</p><details open><summary>{evidence.data.gaps.length} checklist gap(s) · expand or collapse</summary><ul className="investigation-gaps">{evidence.data.gaps.map((gap, index) => <li key={index}>{gap.severity} · {gap.code}: {gap.detail}<br /><small>Source: {gap.source_type}{gap.source_ref ? ` · ${gap.source_ref}` : " · Reference not recorded"}</small></li>)}</ul></details></> : <p>No checklist gaps were returned. Review the underlying evidence before making a decision.</p>}
          <p><Link prefetch={false} href={link("/workspace/evidence-briefing")}>Review evidence briefing →</Link></p></>}
      </section>
      <section className="panel"><h2>Previously recorded risk indicators</h2>
        {outputs?.error && <ErrorState error={outputs.error} what="Recorded indicators" />}
        {outputs?.data && <>{indicators.length === 0 ? <Empty>No recognized recorded indicators were returned. This does not establish that the case has no risk or that detection ran.</Empty> : <ul>{indicators.map(({ output, finding }, index) => <li key={`${output.output_id}-${finding.claim_id}-${index}`}>{finding.claim} · {finding.support_status} · snapshot {output.snapshot_id}</li>)}</ul>}
          <p><Link prefetch={false} href={link("/workspace/recorded-risk")}>Review recorded indicators and calculations →</Link></p></>}
      </section>
      <section className="panel"><h2>Continue the investigation</h2><ul className="investigation-links">
        <li><Link prefetch={false} href={link("/workspace/recorded-transactions")}>Review recorded transactions →</Link></li>
        <li><Link prefetch={false} href={link("/workspace/recorded-provenance")}>Review document provenance →</Link></li>
        <li><Link prefetch={false} href={link("/workspace/recorded-review")}>Compare recorded report changes →</Link></li>
        <li><Link prefetch={false} href={link("/workspace/recorded-monitor")}>Inspect recorded investigation runs →</Link></li>
        <li><Link prefetch={false} href={`/cases/${selected}`}>Open the existing case workflow →</Link></li>
      </ul></section>
    </>}
  </section>;
}
