import Link from "next/link";
import { When } from "@/components/ui";

type Counts = Record<string, number>;
interface Duration {
  sample_count: number; mean_seconds: number | null; min_seconds: number | null;
  max_seconds: number | null; invalid_negative_samples: number;
}
interface Rate { numerator: number; denominator: number; value: number | null }
export interface DashboardData {
  generated_at: string; window_start: string; window_days: number; scope: string; can_reconcile: boolean;
  cases_by_status: Counts; cases_created: number; time_to_first_completion: Duration; review_turnaround: Duration;
  review_decisions: Counts; model_requests_by_status: Counts; model_requests_by_purpose: Counts;
  model_requests: number; model_tokens: number; estimated_model_cost_by_currency: Record<string, string>;
  model_latency: Duration; model_slices_truncated: boolean;
  model_slices: { case_type: string; config_version: string; currency: string; requests: number; tokens: number; estimated_cost: string }[];
  agent_outputs_by_status: Counts; analyst_edit_origin_versions: number; output_rejection_rate: Rate;
  output_gap_rate: Rate; output_escalation_rate: Rate; tool_outcomes: Counts; current_evidence_freshness: Counts;
  runs_by_status: Counts; run_recovery_transitions: number; external_operations: Counts;
  recent_runs_truncated: boolean; recent_steps_truncated: boolean;
  recent_runs: { run_id: string; graph_version: string; status: string; case_href: string | null;
    elapsed_seconds: number | null; attempt: number; blocker_code: string | null;
    estimated_model_cost_by_currency: Record<string, string>;
    steps: { node: string; status: string; attempt: number; elapsed_seconds: number | null }[] }[];
  connector_health: { source: string; state: string; last_observed_at: string | null; last_error_code: string | null;
    logical_calls: number; failed_calls: number; retry_attempts: number; mean_latency_ms: number | null;
    failure_rate: Rate; recent_observation_seconds: number }[] | null;
  unavailable: Record<string, string>;
}

function elapsed(seconds: number | null): string {
  if (seconds === null) return "N/A";
  return seconds >= 3600 ? `${(seconds / 3600).toFixed(2)} h` : `${(seconds / 60).toFixed(2)} min`;
}
function money(values: Record<string, string>): string {
  return Object.entries(values).map(([currency, amount]) => `${currency} ${amount}`).join("; ") || "N/A";
}
function rate(value: Rate): string {
  return value.value === null ? `N/A (${value.numerator}/${value.denominator})`
    : `${(value.value * 100).toFixed(1)}% (${value.numerator}/${value.denominator})`;
}
function CountsTable({ title, values }: { title: string; values: Counts }) {
  const entries = Object.entries(values);
  return <div className="panel"><h3>{title}</h3>{entries.length ? <table><caption>{title}</caption>
    <thead><tr><th scope="col">Category</th><th scope="col">Count</th></tr></thead>
    <tbody>{entries.map(([label, count]) => <tr key={label}><th scope="row">{label}</th><td>{count}</td></tr>)}</tbody>
  </table> : <p>No observations in this scope.</p>}</div>;
}
function DurationCard({ title, value }: { title: string; value: Duration }) {
  return <div className="panel"><h3>{title}</h3><p>Mean: <strong>{elapsed(value.mean_seconds)}</strong></p>
    <p>{value.sample_count} valid samples; range {elapsed(value.min_seconds)} to {elapsed(value.max_seconds)}.</p>
    {value.invalid_negative_samples ? <p>{value.invalid_negative_samples} invalid negative durations omitted.</p> : null}</div>;
}

export function OperationsDashboard({ data }: { data: DashboardData }) {
  return <section aria-labelledby="dashboard-heading">
    <h2 id="dashboard-heading">Operational dashboard</h2>
    <p>Scope: <strong>{data.scope}</strong>. Generated <When iso={data.generated_at} />.</p>
    <nav aria-label="Reporting window"><Link href="/operations?days=1">1 day</Link>{" · "}
      <Link href="/operations?days=7">7 days</Link>{" · "}<Link href="/operations?days=28">28 days</Link></nav>
    <p>Event window: <When iso={data.window_start} /> to <When iso={data.generated_at} />.
      Backlog and current evidence freshness include records of all ages.</p>
    <div className="panel"><h3>Volumes and model usage</h3><dl>
      <dt>Cases created in window</dt><dd>{data.cases_created}</dd>
      <dt>Recorded model requests</dt><dd>{data.model_requests}</dd><dt>Recorded model tokens</dt><dd>{data.model_tokens}</dd>
      <dt>Estimated model cost</dt><dd>{money(data.estimated_model_cost_by_currency)}</dd>
      <dt>Recovery/requeue transitions</dt><dd>{data.run_recovery_transitions}</dd>
    </dl><p>Costs are configured estimates, not invoices. Execution gateways continue to enforce existing budgets.</p></div>
    <DurationCard title="Time to first completion" value={data.time_to_first_completion} />
    <DurationCard title="Review turnaround" value={data.review_turnaround} />
    <DurationCard title="Recorded model-attempt latency" value={data.model_latency} />
    <p>Case and review durations are calendar elapsed time, including waiting. They do not measure active human work.</p>
    {data.connector_health === null ? <p>Connector observations require platform-operator permission.</p> :
      <section aria-labelledby="connector-heading"><h3 id="connector-heading">Observed connector status</h3>
        <p>Latest recorded call, not a live availability probe. A sample older than five minutes is stale.
          Observations cover HTTP transport and JSON envelopes; later business-schema checks can still fail.</p>
        {data.connector_health.map(connector => <div className="panel" key={connector.source}>
          <h4>{connector.source}</h4><p>Status: <strong>{connector.state}</strong></p>
          <p>Last observation: <When iso={connector.last_observed_at} />; error: {connector.last_error_code ?? "None recorded"}.</p>
          <p>{connector.logical_calls} logical calls; {connector.retry_attempts} retries; failure rate {rate(connector.failure_rate)}.</p>
          <p>Mean logical-call latency: {connector.mean_latency_ms === null ? "N/A" : `${connector.mean_latency_ms.toFixed(1)} ms`}.</p>
        </div>)}
      </section>}
    <section aria-labelledby="quality-heading"><h3 id="quality-heading">Quality proxies</h3><dl>
      <dt>Outputs with rejected claims</dt><dd>{rate(data.output_rejection_rate)}</dd>
      <dt>Outputs containing gap findings</dt><dd>{rate(data.output_gap_rate)}</dd>
      <dt>Escalated outputs</dt><dd>{rate(data.output_escalation_rate)}</dd>
      <dt>Versions with analyst-edit origin</dt><dd>{data.analyst_edit_origin_versions}</dd>
    </dl><p>These are output-level monitoring proxies, not accuracy or claim-support precision.
      Analyst-edit origin includes reopening copies and does not prove a reviewer correction.</p></section>
    <CountsTable title="Current case backlog" values={data.cases_by_status} />
    <CountsTable title="Current workflow states" values={data.runs_by_status} />
    <CountsTable title="Review decisions in window" values={data.review_decisions} />
    <CountsTable title="Agent-output states in window" values={data.agent_outputs_by_status} />
    <CountsTable title="Tool decisions in window" values={data.tool_outcomes} />
    <CountsTable title="Model-request states in window" values={data.model_requests_by_status} />
    <CountsTable title="Model attempts, retries and repairs" values={data.model_requests_by_purpose} />
    <CountsTable title="Current evidence freshness" values={data.current_evidence_freshness} />
    <CountsTable title="External-operation states updated in window" values={data.external_operations} />
    <section aria-labelledby="slices-heading"><h3 id="slices-heading">Model usage by case type and configuration</h3>
      <p>Configuration identifies the recorded gateway configuration version, not a full deployment fingerprint.
        UNLINKED requests have no case association, including evaluations.</p>
      {data.model_slices.length ? <div style={{ overflowX: "auto" }}><table><caption>Model usage slices in window</caption>
        <thead><tr><th scope="col">Case type</th><th scope="col">Configuration</th><th scope="col">Requests</th>
          <th scope="col">Tokens</th><th scope="col">Estimated cost</th></tr></thead><tbody>
          {data.model_slices.map((slice, index) => <tr key={`${slice.case_type}-${slice.config_version}-${slice.currency}-${index}`}>
            <th scope="row">{slice.case_type}</th><td>{slice.config_version}</td><td>{slice.requests}</td>
            <td>{slice.tokens}</td><td>{slice.currency} {slice.estimated_cost}</td></tr>)}
        </tbody></table></div> : <p>No model observations in this window.</p>}
      {data.model_slices_truncated ? <p>Showing the 100 largest slices; totals above include every slice.</p> : null}
    </section>
    <section aria-labelledby="runs-heading"><h3 id="runs-heading">Recent workflows and steps</h3>
      <p>Latest 20 runs across all ages; up to 200 recent step records. Run cost covers its recorded lifetime.</p>
      {data.recent_runs.length === 0 ? <p>No workflows in this scope.</p> : null}
      {data.recent_runs.map(run => <article className="panel" key={run.run_id}>
        <h4><Link href={`/workflows/${run.run_id}`}>Workflow {run.run_id.slice(0, 8)}</Link></h4>
        <p>{run.status}; graph {run.graph_version}; attempt {run.attempt}; elapsed {elapsed(run.elapsed_seconds)}.</p>
        <p>Blocker code: {run.blocker_code ?? "None recorded"}; estimated model cost: {money(run.estimated_model_cost_by_currency)}.</p>
        {run.case_href ? <p><Link href={run.case_href}>Open permitted case</Link></p> : null}
        <details><summary>Step records ({run.steps.length})</summary><table><caption>Recorded steps for workflow {run.run_id.slice(0, 8)}</caption>
          <thead><tr><th scope="col">Step</th><th scope="col">State</th><th scope="col">Attempt</th><th scope="col">Elapsed</th></tr></thead>
          <tbody>{run.steps.map((step, index) => <tr key={`${step.node}-${index}`}><th scope="row">{step.node}</th>
            <td>{step.status}</td><td>{step.attempt}</td><td>{elapsed(step.elapsed_seconds)}</td></tr>)}</tbody></table></details>
      </article>)}
      {data.recent_runs_truncated || data.recent_steps_truncated ? <p>Additional run or step records were omitted from this bounded view.</p> : null}
    </section>
    <section aria-labelledby="unmeasured-heading"><h3 id="unmeasured-heading">Unavailable measurements</h3><dl>
      {Object.entries(data.unavailable).map(([metric, reason]) => <div key={metric}><dt>{metric.replaceAll("_", " ")}</dt>
        <dd>N/A — {reason}</dd></div>)}
    </dl></section>
  </section>;
}
