import { randomUUID } from "node:crypto";
import { DecisionForm } from "@/components/DecisionForm";
import Link from "next/link";
import { decide } from "@/app/actions";
import { FindingTag, type FindingKind } from "@/components/StatusBadge";
import { SubmitButton } from "@/components/SubmitButton";
import { ErrorState, Flash, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Detail {
  approval_request_id: string; case_id: string; status: string; status_reason: string | null;
  report_version_id: string; snapshot_id: string; requested_by: string; reviewer_id: string | null;
  matrix_version: string; created_at: string; evidence_drift: string[];
  decision: { decision: string; rationale: string; reviewer_id: string; on_behalf_of: string | null; mfa_satisfied: boolean; effect: string; decided_at: string } | null;
  snapshot?: { version_no: number; completeness_status: string; open_conflicts: number; gaps: { code: string; severity: string; detail: string }[] };
  report?: {
    version_no: number; sections: { section_id: string; title: string; text: string }[];
    claims: { claim_id: string; claim_type: FindingKind; text: string; support_status: string; evidence_refs: string[]; citation_refs: string[] }[];
    limitations: Record<string, string[]>; diff_from_previous?: { section_id: string; change: string; diff: string[] }[];
  };
}

export default async function ApprovalDetail({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const { id } = await params;
  const sp = await searchParams;
  const { data: d, error } = await load<Detail>(`/api/v1/approvals/${id}`);
  if (error) return <ErrorState error={error} what="Review request" />;
  return (
    <section aria-labelledby="h">
      <p><Link href="/approvals">← Back to approval center</Link></p>
      <h1 id="h">Review request {id.slice(0, 8)} - {d.status}</h1>
      <p><Link href={`/cases/${d.case_id}`}>Open case</Link> - submitted <When iso={d.created_at} /> - matrix {d.matrix_version}</p>
      <p><Link href={`/workspace/recorded-review?${new URLSearchParams({ case: d.case_id })}`}>Compare recorded report versions →</Link></p>
      <Flash ok={sp.ok} error={sp.error} />
      {d.evidence_drift.length ? (
        <p role="alert" className="flash flash-error">Evidence changed after submission ({d.evidence_drift.join(", ")}).
          Any decision will be refused and the submission invalidated.</p>
      ) : null}
      {d.snapshot ? (
        <div className="panel">
          <h2>Bound evidence snapshot v{d.snapshot.version_no}: {d.snapshot.completeness_status}</h2>
          <p>Open conflicts recorded in this snapshot: {d.snapshot.open_conflicts}</p>
          {d.snapshot.gaps.length ? <ul>{d.snapshot.gaps.map((g, i) => <li key={i}>{g.severity}: {g.code} - {g.detail}</li>)}</ul>
            : <p className="muted">No gaps were recorded in this snapshot.</p>}
        </div>
      ) : <p role="alert" className="warn">The evidence snapshot was not returned for this review request.</p>}
      {d.report ? (
        <>
          <h2>Report version {d.report.version_no} (frozen for this review)</h2>
          {d.report.sections.map((s) => <div key={s.section_id} className="panel"><h3>{s.title}</h3>
            <p style={{ whiteSpace: "pre-wrap" }}>{s.text}</p></div>)}
          <h2>Claims and evidence</h2>
          {d.report.claims.length === 0 && <p>No claims were returned for this report version.</p>}
          <ul>{d.report.claims.map((c) => (
            <li key={c.claim_id} className="claim"><FindingTag kind={c.claim_type} /> {c.text}{" "}
              <span className="muted">[{c.support_status}; {[...c.evidence_refs, ...c.citation_refs].join(", ")}]</span></li>))}</ul>
          {d.report.diff_from_previous?.length ? (
            <details><summary>Differences from the previous version</summary>
              {d.report.diff_from_previous.map((x) => <div key={x.section_id}><strong>{x.section_id}</strong> ({x.change})
                <pre className="diff">{x.diff.join("\n")}</pre></div>)}</details>) : null}
          <h2>Recorded report limitations</h2>
          {Object.values(d.report.limitations ?? {}).some((items) => items.length > 0) ? <dl>{Object.entries(d.report.limitations).map(([category, items]) => items.length > 0 ? <div key={category}><dt>{category}</dt><dd><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></dd></div> : null)}</dl> : <p>No report limitations were returned.</p>}
        </>
      ) : <p role="alert" className="warn">The report version was not returned for this review request.</p>}
      {d.decision ? (
        <div className="panel">
          <h2>Decision: {d.decision.decision}</h2>
          <p>{d.decision.rationale}</p>
          <p className="muted">By {d.decision.reviewer_id.slice(0, 8)}{d.decision.on_behalf_of ? ` on behalf of ${d.decision.on_behalf_of.slice(0, 8)}` : ""},
            MFA {d.decision.mfa_satisfied ? "satisfied" : "not satisfied"}, effect {d.decision.effect}, <When iso={d.decision.decided_at} /></p>
        </div>
      ) : null}
      {d.status === "PENDING" && d.report && d.snapshot ? (
        <DecisionForm action={decide} refreshHref={`/approvals/${id}`}>
          <h2>Your decision</h2>
          <input type="hidden" name="approval_request_id" value={id} />
          <input type="hidden" name="report_version_id" value={d.report_version_id} />
          <input type="hidden" name="snapshot_id" value={d.snapshot_id} />
          <input type="hidden" name="key" value={randomUUID()} />
          <fieldset>
            <legend>Decision</legend>
            {[["APPROVE", "Approve"], ["RETURN_FOR_REWORK", "Return for rework"], ["REQUEST_INFORMATION", "Request information"],
              ["REJECT", "Reject (effect per configured policy)"]].map(([value, label]) => (
              <div key={value}><input type="radio" id={`d-${value}`} name="decision" value={value} required />{" "}
                <label htmlFor={`d-${value}`}>{label}</label></div>
            ))}
          </fieldset>
          <label htmlFor="rationale">Rationale (required, recorded with your identity and the reviewed versions)</label>
          <textarea id="rationale" name="rationale" required minLength={10} maxLength={2000} />
          <p className="muted">You cannot decide on work you submitted or authored. MFA is required.</p>
          <p><Link href="/security">Verify an enrolled authenticator before recording your decision</Link></p>
          <SubmitButton>Record decision</SubmitButton>
        </DecisionForm>
      ) : null}
      {d.status === "PENDING" && (!d.report || !d.snapshot) ? <p className="warn">The decision form is unavailable until the report and evidence snapshot can be reviewed.</p> : null}
    </section>
  );
}
