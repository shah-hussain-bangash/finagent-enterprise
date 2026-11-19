import Link from "next/link";
import { FindingTag, type FindingKind } from "@/components/StatusBadge";
import { Empty, ErrorState, Json, Tabs, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Finding { claim_id: string; claim_type: FindingKind; claim: string; support_status: string; evidence_refs: string[]; value?: string | null }
interface Output {
  output_id: string; capability_id: string; capability_version: string; status: string; snapshot_id: string;
  findings: Finding[]; limitations: Record<string, string[]>; recommendations: string[];
  validation: { rejected?: { claim_id: string; reasons: string[] }[]; boundary_violations?: string[]; notes?: string[] };
  lineage: Record<string, unknown>; usage: Record<string, unknown>; created_at: string;
}

export default async function Agents({ params }: { params: Promise<{ id: string }> }) {
  await requireSession();
  const { id } = await params;
  const { data, error } = await load<{ items: Output[] }>(`/api/v1/cases/${id}/agent-outputs`);
  const seenCapabilities = new Set<string>();
  const outputs = (data?.items ?? []).slice().reverse().map((output) => {
    const latest = !seenCapabilities.has(output.capability_id);
    seenCapabilities.add(output.capability_id);
    return { output, latest };
  });
  return (
    <section aria-labelledby="h">
      <h1 id="h">Agent findings</h1>
      <Tabs caseId={id} active="/agents" />
      <p><Link href={`/workspace/recorded-risk?${new URLSearchParams({ case: id })}`}>Review recorded risk indicators →</Link></p>
      <p><Link href={`/workspace/evidence-briefing?${new URLSearchParams({ case: id })}`}>Review current case evidence →</Link></p>
      <p className="muted">Outputs refer to their recorded evidence snapshots. Current case evidence may differ.</p>
      <p className="muted">Each finding is labelled: <FindingTag kind="FACT" /> sourced fact, <FindingTag kind="DETERMINISTIC_RESULT" />
        computed by validated services (never by the model), <FindingTag kind="INTERPRETATION" /> AI interpretation for
        review, <FindingTag kind="GAP" /> missing or contradictory evidence. Rejected claims never reach the report.</p>
      {error ? <ErrorState error={error} what="Agent findings" /> : null}
      {data && data.items.length === 0 ? <Empty>No recorded agent outputs were returned. This does not establish whether an investigation ran.</Empty> : null}
      {outputs.length > 0 && <p>Showing {outputs.length} recorded output(s), newest first. The newest output for each capability is expanded; open an earlier output to review it.</p>}
      {outputs.map(({ output: o, latest }) => (
        <details key={o.output_id} className="panel agent-output" open={latest}>
          <summary>{o.capability_id} {o.capability_version} · {o.status} · <When iso={o.created_at} /></summary>
        <article aria-labelledby={`o-${o.output_id}`}>
          <h2 id={`o-${o.output_id}`}>{o.capability_id} {o.capability_version} -{" "}
            <span className={`badge badge-${o.status.toLowerCase()}`}>{o.status}</span></h2>
          <p className="muted"><When iso={o.created_at} /> - snapshot {o.snapshot_id.slice(0, 8)}</p>
          {o.validation.boundary_violations?.length ? (
            <p role="alert" className="flash flash-error">Rejected: the output crossed an authority boundary
              ({o.validation.boundary_violations.join(", ")}). Escalated to a human.</p>
          ) : null}
          {o.findings.length === 0 ? <Empty>No accepted findings.</Empty> : (
            <ul>
              {o.findings.map((f) => (
                <li key={f.claim_id} className="claim">
                  <FindingTag kind={f.claim_type} /> {f.claim} <span className="muted">[{f.support_status}; {f.evidence_refs.join(", ")}]</span>
                </li>
              ))}
            </ul>
          )}
          {o.validation.rejected?.length ? (
            <details><summary>{o.validation.rejected.length} claim(s) rejected by deterministic validation</summary>
              <ul>{o.validation.rejected.map((r) => <li key={r.claim_id}>{r.claim_id}: {r.reasons.join(", ")}</li>)}</ul>
            </details>
          ) : null}
          {Object.values(o.limitations ?? {}).some((v) => v.length) ? (
            <details><summary>Limitations</summary><Json value={o.limitations} /></details>
          ) : null}
          {o.recommendations.length ? (
            <details><summary>Reviewer-facing suggestions (not decisions)</summary>
              <ul>{o.recommendations.map((r, i) => <li key={i}>{r}</li>)}</ul></details>
          ) : null}
          <details><summary>Lineage and usage</summary><Json value={{ lineage: o.lineage, usage: o.usage }} /></details>
        </article></details>
      ))}
    </section>
  );
}
