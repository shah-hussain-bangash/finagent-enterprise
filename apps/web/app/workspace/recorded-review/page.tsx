import Link from "next/link";
import { ConnectedCaseNav } from "@/components/ConnectedCaseNav";
import { Empty, ErrorState, When } from "@/components/ui";
import { api, ApiRequestError, listCases } from "@/lib/api";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Recorded report changes | FinAgent Enterprise" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface History { version_no: number; origin: string; author_label: string; created_at: string; valid: boolean; edit_note: string | null }
interface ReportList { report: { status: string; current_version_no: number } | null; versions: History[] }
interface Version { version_no: number; snapshot_id: string; watermark: string | null; validation: { valid: boolean; problems?: { code: string; section?: string }[] }; diff_from_previous?: { section_id: string; change: string; diff: string[] }[] }

export default async function RecordedReview({ searchParams }: { searchParams: Promise<{ case?: string; version?: string; cursor?: string }> }) {
  const params = await searchParams;
  const value = typeof params.version === "string" ? params.version : "";
  const returnSelection = /^[1-9]\d{0,8}$/.test(value) ? `&version=${value}` : "";
  const returnTo = `/workspace/recorded-review?case=${typeof params.case === "string" ? params.case : ""}${returnSelection}`;
  await requireSession(returnTo);
  const selected = typeof params.case === "string" ? params.case : "";
  const cursor = typeof params.cursor === "string" ? params.cursor : undefined;
  const validCase = UUID.test(selected);
  let cases: Awaited<ReturnType<typeof listCases>> | null = null;
  let caseError: ApiRequestError | null = null;
  let reports: ReportList | null = null;
  let reportsError: ApiRequestError | null = null;
  let version: Version | null = null;
  let versionError: ApiRequestError | null = null;
  try { cases = await listCases(undefined, cursor, returnTo); }
  catch (error) { if (!(error instanceof ApiRequestError)) throw error; caseError = error; }
  if (validCase) {
    try { reports = await api<ReportList>(`/api/v1/cases/${selected}/reports`, { returnTo }); }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; reportsError = error; }
  }
  const requested = typeof params.version === "string" && /^[1-9]\d{0,8}$/.test(params.version) ? Number(params.version) : null;
  const chosen = params.version ? requested : reports?.report?.current_version_no ?? null;
  if (chosen && reports?.versions.some((item) => item.version_no === chosen)) {
    try { version = await api<Version>(`/api/v1/cases/${selected}/reports/versions/${chosen}`, { returnTo }); }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; versionError = error; }
  }
  const link = (value: Record<string, string>) => `/workspace/recorded-review?${new URLSearchParams(value)}`;
  return <section className="connected-workspace" aria-labelledby="recorded-review-title">
    <p><Link href="/workspace" prefetch={false}>← Back to workspace preview</Link></p>
    {validCase && <p><Link href={`/workspace/case-overview?${new URLSearchParams({ case: selected })}`} prefetch={false}>← Back to case overview</Link></p>}
    <h1 id="recorded-review-title">Recorded report changes</h1>
    {reports && <ConnectedCaseNav caseId={selected} active="report" />}
    <div className="panel"><strong>Connected, read-only view</strong><p>Review recorded report versions and changes from each previous version. This view does not submit a decision or verify approval readiness.</p></div>
    <section className="panel"><h2>Choose an accessible case</h2>
      {caseError && <ErrorState error={caseError} what="Cases" />}
      {cases?.items.length === 0 && <Empty>No cases are available on this page.</Empty>}
      {cases && cases.items.length > 0 && <ul>{cases.items.map((item) => <li key={item.case_id}><Link prefetch={false} aria-current={selected === item.case_id ? "true" : undefined} href={link({ case: item.case_id, ...(cursor ? { cursor } : {}) })}>Case {item.case_id.slice(0, 8)}</Link>{selected === item.case_id && <span className="case-selected">Selected</span>} · {item.status}</li>)}</ul>}
      {cases?.next_cursor && <p><Link prefetch={false} href={link({ ...(validCase ? { case: selected, ...(params.version ? { version: params.version } : {}) } : {}), cursor: cases.next_cursor })}>Next cases →</Link></p>}
      {cursor && <p><Link prefetch={false} href={link(validCase ? { case: selected, ...(params.version ? { version: params.version } : {}) } : {})}>← First cases</Link></p>}
    </section>
    {selected && !validCase && <p role="alert">The selected case identifier is invalid.</p>}
    {validCase && <section className="panel"><h2>Report version history</h2>
      {reportsError && <ErrorState error={reportsError} what="Report history" />}
      {reports && !reports.report && <Empty>No report was returned for this case.</Empty>}
      {reports?.report && <><p>Report status: {reports.report.status} · Current version: {reports.report.current_version_no}</p>
        {reports.versions.length === 0 ? <Empty>No report versions were returned.</Empty> : <ul>{reports.versions.map((item) => <li key={item.version_no}>
          <Link prefetch={false} aria-current={chosen === item.version_no ? "true" : undefined} href={`${link({ case: selected, version: String(item.version_no), ...(cursor ? { cursor } : {}) })}#version-details`}>Version {item.version_no}</Link> · {item.origin} · {item.author_label} · <When iso={item.created_at} /> · {item.valid ? "Validation passed" : "Validation problems"}{item.edit_note ? ` · ${item.edit_note}` : ""}
        </li>)}</ul>}</>}
      {params.version && reports && (!chosen || !reports.versions.some((item) => item.version_no === chosen)) && <p role="alert">The selected version was not returned for this case.</p>}
      {versionError && <ErrorState error={versionError} what="Report version" />}
    </section>}
    {version && <section className="panel" id="version-details"><h2>Changes for version {version.version_no}</h2>
      <p>Evidence snapshot: {version.snapshot_id}</p>{version.watermark && <p>{version.watermark}</p>}
      <p>{version.validation.valid ? "Recorded validation passed." : "Recorded validation problems:"}</p>
      {!version.validation.valid && <ul>{version.validation.problems?.map((problem, index) => <li key={index}>{problem.code}{problem.section ? ` · ${problem.section}` : ""}</li>)}</ul>}
      {version.diff_from_previous?.length ? version.diff_from_previous.map((change) => <div key={change.section_id}><h3>{change.section_id} · {change.change}</h3><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{change.diff.join("\n")}</pre></div>) : <Empty>No changes from a previous version were returned.</Empty>}
      <p><Link prefetch={false} href={`/cases/${selected}/report?v=${version.version_no}`}>Open full report in the existing portal →</Link></p>
      <p><Link prefetch={false} href="/approvals">Open existing approvals →</Link></p>
    </section>}
  </section>;
}
