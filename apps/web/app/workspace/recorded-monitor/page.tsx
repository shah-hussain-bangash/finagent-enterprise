import Link from "next/link";
import { Empty, ErrorState, When } from "@/components/ui";
import { api, ApiRequestError, listCases } from "@/lib/api";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";
export const metadata = { title: "Recorded investigation runs | FinAgent Enterprise" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
interface RunSummary { run_id: string; status: string; status_reason: string; workflow_type: string; created_at: string; attempt: number }
interface Step { node: string; status: string; attempt: number; error_code: string | null; started_at: string | null; finished_at: string | null }
interface Run { run_id: string; case_id: string; status: string; status_reason: string; workflow_type: string; graph_version: string; attempt: number; max_attempts: number; next_action: string | null; steps: Step[]; blockers: { code: string; reason?: string }[] }

export default async function RecordedMonitor({ searchParams }: { searchParams: Promise<{ case?: string; run?: string; cursor?: string }> }) {
  await requireSession();
  const params = await searchParams;
  const selected = typeof params.case === "string" ? params.case : "";
  const runId = typeof params.run === "string" ? params.run : "";
  const cursor = typeof params.cursor === "string" ? params.cursor : undefined;
  const validCase = UUID.test(selected);
  let cases: Awaited<ReturnType<typeof listCases>> | null = null;
  let caseError: ApiRequestError | null = null;
  let runs: RunSummary[] | null = null;
  let runsError: ApiRequestError | null = null;
  let run: Run | null = null;
  let runError: ApiRequestError | null = null;
  try { cases = await listCases(undefined, cursor); }
  catch (error) { if (!(error instanceof ApiRequestError)) throw error; caseError = error; }
  if (validCase) {
    try { runs = (await api<{ items: RunSummary[] }>(`/api/v1/cases/${selected}/workflows`)).items; }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; runsError = error; }
  }
  if (UUID.test(runId) && runs?.some((item) => item.run_id === runId)) {
    try { run = await api<Run>(`/api/v1/workflows/${runId}`); }
    catch (error) { if (!(error instanceof ApiRequestError)) throw error; runError = error; }
  }
  const link = (value: Record<string, string>) => `/workspace/recorded-monitor?${new URLSearchParams(value)}`;
  return <section aria-labelledby="monitor-title">
    <p><Link href="/workspace" prefetch={false}>← Back to workspace preview</Link></p>
    <h1 id="monitor-title">Recorded investigation runs</h1>
    <div className="panel"><strong>Connected, read-only view</strong><p>Shows recorded workflow steps and attempts for an accessible case. Refresh to see newer status. Model usage and cost are not shown here.</p></div>
    <section className="panel"><h2>Choose an accessible case</h2>
      {caseError && <ErrorState error={caseError} what="Cases" />}
      {cases?.items.length === 0 && <Empty>No cases are available on this page.</Empty>}
      {cases && cases.items.length > 0 && <ul>{cases.items.map((item) => <li key={item.case_id}><Link prefetch={false} href={link({ case: item.case_id, ...(cursor ? { cursor } : {}) })}>Case {item.case_id.slice(0, 8)}</Link> · {item.status}</li>)}</ul>}
      {cases?.next_cursor && <p><Link prefetch={false} href={link({ cursor: cases.next_cursor })}>Next cases →</Link></p>}
      {cursor && <p><Link prefetch={false} href="/workspace/recorded-monitor">← First cases</Link></p>}
    </section>
    {selected && !validCase && <p role="alert">The selected case identifier is invalid.</p>}
    {validCase && <section className="panel"><h2>Case runs</h2>
      {runsError && <ErrorState error={runsError} what="Case runs" />}
      {runs?.length === 0 && <Empty>No recorded runs were returned for this case.</Empty>}
      {runs && runs.length > 0 && <ul>{runs.map((item) => <li key={item.run_id}><Link prefetch={false} href={link({ case: selected, run: item.run_id, ...(cursor ? { cursor } : {}) })}>Run {item.run_id.slice(0, 8)}</Link> · {item.workflow_type} · {item.status} · attempt {item.attempt} · <When iso={item.created_at} /></li>)}</ul>}
      {runId && runs && (!UUID.test(runId) || !runs.some((item) => item.run_id === runId)) && <p role="alert">This run was not returned for the selected case.</p>}
      {runError && <ErrorState error={runError} what="Run details" />}
    </section>}
    {run && run.case_id === selected && <section className="panel"><h2>Run {run.run_id.slice(0, 8)}</h2>
      <p>{run.workflow_type} · {run.graph_version} · {run.status} · attempt {run.attempt}/{run.max_attempts}</p>
      <p>Status reason: {run.status_reason}</p>{run.next_action && <p>Next action: {run.next_action}</p>}
      {run.blockers.length > 0 && <><h3>Blockers</h3><ul>{run.blockers.map((item, index) => <li key={index}>{item.code}{item.reason ? `: ${item.reason}` : ""}</li>)}</ul></>}
      <h3>Recorded steps</h3>
      {run.steps.length === 0 ? <Empty>No recorded steps were returned.</Empty> : <div style={{ overflowX: "auto" }}><table><caption>Recorded workflow steps</caption><thead><tr><th>Step</th><th>Status</th><th>Attempt</th><th>Error</th><th>Started</th><th>Finished</th></tr></thead><tbody>{run.steps.map((step, index) => <tr key={index}><td>{step.node}</td><td>{step.status}</td><td>{step.attempt}</td><td>{step.error_code ?? "None recorded"}</td><td><When iso={step.started_at} /></td><td><When iso={step.finished_at} /></td></tr>)}</tbody></table></div>}
      <p><Link prefetch={false} href={`/workflows/${run.run_id}`}>Open full execution and recovery view →</Link></p>
    </section>}
  </section>;
}
