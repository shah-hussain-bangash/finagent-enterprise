import Link from "next/link";
import { startEvaluation } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { Empty, ErrorState, Flash, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Run {
  evaluation_id: string; dataset: string; dataset_version: string; config_hash: string; status: string;
  gate: string; metrics: Record<string, { value: number; threshold: number | null; threshold_status: string; passed: boolean | null }>;
  created_at: string;
}

export default async function Evaluations({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireSession();
  const sp = await searchParams;
  const { data, error } = await load<{ items: Run[]; datasets: string[] }>("/api/v1/evaluations");
  return (
    <section aria-labelledby="h">
      <h1 id="h">Evaluation dashboard</h1>
      <p className="muted">SYNTHETIC evaluation sets. Thresholds are PROPOSED and unapproved (AI-D04): a "pass" here is not an
        acceptance decision.</p>
      <Flash ok={sp.ok} error={sp.error} />
      {error ? <ErrorState error={error} what="Evaluations" /> : null}
      {data ? (
        <form action={startEvaluation} className="inline panel">
          <label htmlFor="dataset">Dataset</label>
          <select id="dataset" name="dataset">{data.datasets.map((d) => <option key={d}>{d}</option>)}</select>
          <SubmitButton>Run evaluation</SubmitButton>
        </form>
      ) : null}
      {data && data.items.length === 0 ? <Empty>No evaluation runs yet.</Empty> : null}
      {data?.items.map((r) => (
        <div key={r.evaluation_id} className="panel">
          <h2>{r.dataset} {r.dataset_version} - gate {r.gate}</h2>
          <p className="muted"><When iso={r.created_at} /> - configuration {r.config_hash.slice(0, 12)} -{" "}
            <Link href={`/evaluations?id=${r.evaluation_id}`}>{r.evaluation_id.slice(0, 8)}</Link></p>
          <table>
            <thead><tr><th>Metric</th><th>Value</th><th>Threshold</th><th>Threshold status</th><th>Result</th></tr></thead>
            <tbody>{Object.entries(r.metrics).map(([k, m]) => (
              <tr key={k}><td>{k}</td><td>{m.value}</td><td>{m.threshold ?? "-"}</td><td>{m.threshold_status}</td>
                <td>{m.passed === null ? "not evaluated" : m.passed ? "meets proposed" : "below proposed"}</td></tr>))}</tbody>
          </table>
        </div>
      ))}
    </section>
  );
}
