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

function Kpi({ label, value, href }: { label: string; value: number | string; href?: string }) {
  return (
    <div className="panel">
      <div className="muted">{label}</div>
      <div className="kpi">{href ? <Link href={href}>{value}</Link> : value}</div>
    </div>
  );
}

export default async function Home() {
  await requireSession();
  const { data, error } = await load<Overview>("/api/v1/reporting/overview");
  return (
    <section aria-labelledby="h">
      <h1 id="h">Overview</h1>
      {error ? <ErrorState error={error} what="Overview" /> : null}
      {data ? (
        <>
          <p className="muted">Counts within your access scope ({data.scope}). Generated {data.generated_at}.</p>
          {data.cases_by_status ? (
            <div className="grid">
              {["ASSIGNED", "INVESTIGATING", "BLOCKED", "AWAITING_REVIEW", "REWORK", "FINALIZING", "COMPLETED"].map((s) => (
                <Kpi key={s} label={s.replace("_", " ").toLowerCase()} value={data.cases_by_status?.[s] ?? 0}
                     href={`/cases?status=${s}`} />
              ))}
              <Kpi label="pending reviews" value={data.pending_reviews ?? 0} href="/approvals" />
              <Kpi label="open escalations" value={data.open_escalations ?? 0} href="/escalations" />
              <Kpi label="documents in quarantine" value={data.open_quarantine ?? 0} />
            </div>
          ) : null}
          {data.runs_by_status ? (
            <>
              <h2>Operations</h2>
              <div className="grid">
                {Object.entries(data.runs_by_status).map(([k, v]) => <Kpi key={k} label={`runs ${k.toLowerCase()}`} value={v} />)}
                <Kpi label="uncertain external operations" value={data.uncertain_operations ?? 0} href="/operations" />
                <Kpi label="open dead letters" value={data.open_dead_letters ?? 0} />
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
