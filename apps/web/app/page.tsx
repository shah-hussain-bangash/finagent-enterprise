import Link from "next/link";
import { ErrorState } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Overview {
  generated_at: string;
  scope: string;
  cases_by_status?: Record<string, number>;
  pending_reviews?: number;
  open_escalations?: number;
  open_quarantine?: number;
  runs_by_status?: Record<string, number>;
  uncertain_operations?: number;
  open_dead_letters?: number;
  model_usage_month?: { requests: number; tokens: number; estimated_cost: string };
}

function Kpi({ label, value, href }: { label: string; value: number | string | undefined; href?: string }) {
  const content = <><span className="overview-kpi-label">{label}</span><span className="overview-kpi-value">{value ?? "Not available"}</span></>;
  return (
    <div className="overview-kpi">{href && value !== undefined ? <Link href={href} className="overview-kpi-content">{content}</Link> : <div className="overview-kpi-content">{content}</div>}</div>
  );
}

export default async function Home() {
  await requireSession();
  const { data, error } = await load<Overview>("/api/v1/reporting/overview");
  return (
    <section className="operational-overview" aria-labelledby="h">
      <div className="overview-header"><div><p className="overview-eyebrow">FINAGENT ENTERPRISE</p><h1 id="h">Your operational overview</h1><p>Review your accessible workload and continue your investigation.</p></div></div>
      {error ? <ErrorState error={error} what="Overview" /> : null}
      {data ? (
        <>
          <p className="muted">Counts within your access scope ({data.scope}). Generated {data.generated_at}.</p>
          {data.cases_by_status ? (
            <section aria-labelledby="overview-cases"><h2 id="overview-cases">Cases and reviews</h2><div className="overview-grid">
              {["ASSIGNED", "INVESTIGATING", "BLOCKED", "AWAITING_REVIEW", "REWORK", "FINALIZING", "COMPLETED"].map((s) => (
                <Kpi key={s} label={s.replaceAll("_", " ").toLowerCase()} value={data.cases_by_status?.[s] ?? 0}
                     href={`/cases?status=${s}`} />
              ))}
              <Kpi label="pending reviews" value={data.pending_reviews} href="/approvals" />
              <Kpi label="open escalations" value={data.open_escalations} href="/escalations" />
              <Kpi label="documents in quarantine" value={data.open_quarantine} />
            </div>
              {typeof data.open_quarantine === "number" && <p className="overview-help">The quarantine count summarizes documents in your access scope. A separate quarantine list is not available here; open a case to review its documents. <Link href="/cases">Open case queue →</Link></p>}
            </section>
          ) : null}
          {data.runs_by_status ? (
            <>
              <h2>Operations</h2>
              <div className="overview-grid">
                {Object.entries(data.runs_by_status).map(([k, v]) => <Kpi key={k} label={`runs ${k.toLowerCase()}`} value={v} />)}
                <Kpi label="uncertain external operations" value={data.uncertain_operations} href="/operations" />
                <Kpi label="open dead letters" value={data.open_dead_letters} />
                {data.model_usage_month ? (
                  <Kpi label="model tokens this month (est. cost)"
                       value={`${data.model_usage_month.tokens} (${data.model_usage_month.estimated_cost})`} />
                ) : null}
              </div>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
