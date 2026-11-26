import Link from "next/link";
import { OperationsDashboard, type DashboardData } from "@/components/OperationsDashboard";
import { reconcile } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { Empty, ErrorState, Flash, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Op {
  operation_id: string; operation_type: string; target_system: string; status: string; attempts: number;
  last_error_code: string | null; run_id: string | null; case_id: string | null; updated_at: string;
}

export default async function Operations({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireSession();
  const sp = await searchParams;
  const uncertainAction = typeof sp.error === "string" && sp.error.startsWith("CONNECTION_ERROR:");
  const days = ["1", "7", "28"].includes(sp.days ?? "") ? sp.days : "7";
  const [{ data, error }, dashboard] = await Promise.all([
    load<{ items: Op[] }>("/api/v1/operations?status=OUTCOME_UNCERTAIN"),
    load<DashboardData>(`/api/v1/reporting/dashboard?days=${days}`),
  ]);
  return (
    <section className="portal-section" aria-labelledby="h">
      <div className="portal-page-heading">
        <p className="portal-eyebrow">OVERSIGHT / OPERATIONS</p>
        <h1 id="h">Operations</h1>
      </div>
      {dashboard.error ? <ErrorState error={dashboard.error} what="Operational dashboard" /> : null}
      {dashboard.data ? <OperationsDashboard data={dashboard.data} /> : null}
      <section aria-labelledby="uncertain-heading">
      <h2 id="uncertain-heading">Uncertain operations</h2>
      <p className="muted">An external effect whose outcome could not be proven is never retried automatically. Verify the
        real outcome in the target system, then record it here (MFA required). The run is handed back for human review.</p>
      <Flash ok={sp.ok} error={sp.error} />
      {uncertainAction && <p className="warn" role="status">Check the recorded operation before another action. <a href="/operations">Reload operations</a></p>}
      {error && (error.status !== 403 || dashboard.data?.can_reconcile !== false) ? <ErrorState error={error} what="Operations" /> : null}
      {data && data.items.length === 0 ? <Empty>No operations need reconciliation.</Empty> : null}
      {data?.items.map((o) => (
        <div key={o.operation_id} className="panel">
          <p><strong>{o.operation_type}</strong> to {o.target_system} - {o.status} after {o.attempts} attempt(s)
            ({o.last_error_code ?? "no code"}), updated <When iso={o.updated_at} />
            {o.run_id ? <> - <Link href={`/workflows/${o.run_id}`}>run {o.run_id.slice(0, 8)}</Link></> : null}</p>
          {!uncertainAction && <form action={reconcile} className="stack">
            <input type="hidden" name="operation_id" value={o.operation_id} />
            <fieldset>
              <legend>Verified outcome</legend>
              <div><input type="radio" id={`a-${o.operation_id}`} name="resolution" value="APPLIED" required />{" "}
                <label htmlFor={`a-${o.operation_id}`}>Applied in the target system</label></div>
              <div><input type="radio" id={`n-${o.operation_id}`} name="resolution" value="NOT_APPLIED" />{" "}
                <label htmlFor={`n-${o.operation_id}`}>Not applied</label></div>
            </fieldset>
            <label htmlFor={`r-${o.operation_id}`}>How it was verified</label>
            <textarea id={`r-${o.operation_id}`} name="reason" required minLength={10} maxLength={500} />
            <SubmitButton>Record reconciliation</SubmitButton>
          </form>}
        </div>
      ))}
      </section>
    </section>
  );
}
