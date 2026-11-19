import Link from "next/link";
import { Empty, ErrorState, When } from "@/components/ui";
import { api, ApiRequestError, listCases } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { EvidenceSearch } from "./EvidenceSearch";

export const dynamic = "force-dynamic";
export const metadata = { title: "Case evidence briefing | FinAgent Enterprise" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface Item { evidence_item_id: string; source_type: string; source_ref: string; source_system: string; version_no: number; freshness: string; completeness: string; as_of: string | null }
interface Gap { code: string; severity: string; source_type: string; source_ref: string | null; detail: string }
interface Conflict { conflict_id: string; field_key: string; status: string }
interface Evidence { items: Item[]; gaps: Gap[]; conflicts: Conflict[]; latest_snapshot: { snapshot_id: string; created_at: string } | null }

export default async function EvidenceBriefing({ searchParams }: { searchParams: Promise<{ case?: string; cursor?: string }> }) {
  await requireSession();
  const params = await searchParams;
  const selected = typeof params.case === "string" ? params.case : "";
  const cursor = typeof params.cursor === "string" ? params.cursor : undefined;
  const validCase = UUID.test(selected);
  let cases: Awaited<ReturnType<typeof listCases>> | null = null;
  let caseError: ApiRequestError | null = null;
  let evidence: Evidence | null = null;
  let evidenceError: ApiRequestError | null = null;
  try { cases = await listCases(undefined, cursor); }
  catch (error) { if (!(error instanceof ApiRequestError)) throw error; caseError = error; }
  if (validCase) {
    try { evidence = await api<Evidence>(`/api/v1/cases/${selected}/evidence`); }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; evidenceError = error; }
  }
  const link = (value: Record<string, string>) => `/workspace/evidence-briefing?${new URLSearchParams(value)}`;
  return <section className="connected-workspace" aria-labelledby="briefing-title">
    <p><Link href="/workspace" prefetch={false}>← Back to workspace preview</Link></p>
    {validCase && <p><Link href={`/workspace/case-overview?${new URLSearchParams({ case: selected })}`} prefetch={false}>← Back to case overview</Link></p>}
    <h1 id="briefing-title">Case evidence briefing</h1>
    <div className="panel"><strong>Connected, read-only evidence overview</strong><p>This structured briefing lists recorded case sources and gaps. It does not use an AI model, answer free-text questions, or make decisions. Current evidence may differ from older snapshots.</p></div>
    <section className="panel"><h2>Choose an accessible case</h2>
      {caseError && <ErrorState error={caseError} what="Cases" />}
      {cases?.items.length === 0 && <Empty>No cases are available on this page.</Empty>}
      {cases && cases.items.length > 0 && <ul>{cases.items.map((item) => <li key={item.case_id}><Link prefetch={false} aria-current={selected === item.case_id ? "true" : undefined} href={link({ case: item.case_id, ...(cursor ? { cursor } : {}) })}>Case {item.case_id.slice(0, 8)}</Link>{selected === item.case_id && <span className="case-selected">Selected</span>} · {item.status}</li>)}</ul>}
      {cases?.next_cursor && <p><Link prefetch={false} href={link({ ...(validCase ? { case: selected } : {}), cursor: cases.next_cursor })}>Next cases →</Link></p>}
      {cursor && <p><Link prefetch={false} href={link(validCase ? { case: selected } : {})}>← First cases</Link></p>}
    </section>
    {selected && !validCase && <p role="alert">The selected case identifier is invalid.</p>}
    {validCase && <section><h2>Briefing for case {selected.slice(0, 8)}</h2>
      {evidenceError && <ErrorState error={evidenceError} what="Case evidence" />}
      {evidence && <>
        <EvidenceSearch key={selected} caseId={selected} />
        <div className="panel"><h3>Current evidence sources</h3>
          {evidence.items.length === 0 ? <Empty>No current evidence was returned. This does not establish that no evidence exists.</Empty> : <ul className="evidence-source-list">{evidence.items.map((item) => <li key={item.evidence_item_id}><strong>{item.source_type}</strong> · {item.source_ref} · {item.source_system} · version {item.version_no} · {item.freshness} / {item.completeness} · as of <When iso={item.as_of} /> · evidence ID {item.evidence_item_id}
            {UUID.test(item.evidence_item_id) && <p><Link prefetch={false} href={`/cases/${selected}/evidence#evidence-${item.evidence_item_id}`}>Open this source in case evidence →</Link></p>}</li>)}</ul>}
        </div>
        <div className="panel"><h3>Recorded evidence gaps</h3>
          {evidence.gaps.length === 0 ? <Empty>No gaps were returned by the current evidence checklist. This is not a finding that the case is complete or risk-free.</Empty> : <><p>These warnings are associated with individual source records; they do not by themselves identify the document selected for investigation.</p><ul>{evidence.gaps.map((gap, index) => <li key={index}><strong>{gap.severity}: {gap.code}</strong> · {gap.source_type}{gap.source_ref ? ` · ${gap.source_ref}` : " · Reference not recorded"} · {gap.detail}</li>)}</ul></>}
          <h3>Recorded conflicts</h3>
          {evidence.conflicts.length === 0 ? <Empty>No conflicts were returned.</Empty> : <ul>{evidence.conflicts.map((conflict) => <li key={conflict.conflict_id}>{conflict.field_key} · {conflict.status} · conflict ID {conflict.conflict_id}</li>)}</ul>}
        </div>
        <div className="panel"><h3>Evidence context</h3>
          {evidence.latest_snapshot ? <p>Latest recorded snapshot: {evidence.latest_snapshot.snapshot_id} · <When iso={evidence.latest_snapshot.created_at} />. The current sources above are not represented as snapshot contents.</p> : <p>No snapshot was returned.</p>}
          <p><Link prefetch={false} href={`/cases/${selected}/evidence`}>Open case evidence and source details →</Link></p>
          <p><Link prefetch={false} href={`/workspace/recorded-risk?${new URLSearchParams({ case: selected })}`}>Open recorded risk indicators →</Link></p>
        </div>
      </>}
    </section>}
  </section>;
}
