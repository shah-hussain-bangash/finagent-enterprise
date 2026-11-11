import { freezeSnapshot, resolveConflict } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { Empty, ErrorState, Flash, Json, Tabs, When } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

interface Item {
  evidence_item_id: string;
  source_type: string;
  source_ref: string;
  version_no: number;
  content_hash: string;
  source_system: string;
  retrieved_at: string;
  as_of: string | null;
  freshness: string;
  completeness: string;
  classification: string;
  change_reason: string | null;
  content: unknown;
}
interface Gap { code: string; severity: string; source_type: string; source_ref: string | null; detail: string }
interface Conflict {
  conflict_id: string; field_key: string; item_a_id: string; item_b_id: string; value_a: string | null;
  value_b: string | null; status: string; resolution: string | null; rationale: string | null;
}
interface Snapshot { snapshot_id: string; version_no: number; completeness_status: string; open_conflicts: number; created_at: string; created_by: string; gaps: Gap[] }
interface Evidence { items: Item[]; gaps: Gap[]; conflicts: Conflict[]; latest_snapshot: Snapshot | null; requirements_version: string }

export default async function EvidencePage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const { id } = await params;
  const sp = await searchParams;
  const { data, error } = await load<Evidence>(`/api/v1/cases/${id}/evidence`);
  const snaps = await load<{ items: Snapshot[] }>(`/api/v1/cases/${id}/snapshots`);
  return (
    <section aria-labelledby="h">
      <h1 id="h">Evidence</h1>
      <Tabs caseId={id} active="/evidence" />
      <Flash ok={sp.ok} error={sp.error} />
      {error ? <ErrorState error={error} what="Evidence" /> : null}
      {data ? (
        <>
          <h2>Gaps and limitations ({data.requirements_version})</h2>
          {data.gaps.length === 0 ? <Empty>No gaps against the required evidence for this alert family.</Empty> : (
            <ul>
              {data.gaps.map((g, i) => (
                <li key={i}><strong>{g.severity === "BLOCKING" ? "Blocking" : "Review"}:</strong> {g.code} - {g.detail}
                  {g.source_ref ? <span className="muted"> ({g.source_type} {g.source_ref})</span> : null}</li>
              ))}
            </ul>
          )}
          <h2>Contradictory records</h2>
          {data.conflicts.length === 0 ? <Empty>No conflicts detected.</Empty> : data.conflicts.map((c) => (
            <div key={c.conflict_id} className="panel">
              <p><strong>{c.field_key}</strong>: source A says <q>{c.value_a}</q>, source B says <q>{c.value_b}</q>.
                Status: <span className={`badge badge-${c.status.toLowerCase()}`}>{c.status}</span></p>
              {c.status === "RESOLVED" ? <p>Resolution {c.resolution}: {c.rationale}</p> : null}
              {c.status === "OPEN" ? (
                <form action={resolveConflict} className="stack">
                  <input type="hidden" name="case_id" value={id} />
                  <input type="hidden" name="conflict_id" value={c.conflict_id} />
                  <label htmlFor={`r-${c.conflict_id}`}>Resolution</label>
                  <select id={`r-${c.conflict_id}`} name="resolution">
                    <option value="USE_A">Use source A</option>
                    <option value="USE_B">Use source B</option>
                    <option value="BOTH_RETAINED_EXPLAINED">Keep both, explained</option>
                    <option value="SOURCE_CORRECTION_REQUESTED">Request a source correction</option>
                  </select>
                  <label htmlFor={`t-${c.conflict_id}`}>Reason (recorded; neither source is deleted)</label>
                  <textarea id={`t-${c.conflict_id}`} name="rationale" required minLength={10} maxLength={1000} />
                  <SubmitButton>Record resolution</SubmitButton>
                </form>
              ) : null}
            </div>
          ))}
          <h2>Current evidence</h2>
          {data.items.length === 0 ? <Empty>No evidence gathered yet. Start an investigation from the case overview.</Empty> : (
            <table>
              <thead><tr><th>Source</th><th>Version</th><th>Freshness / completeness</th><th>As of</th><th>Content</th></tr></thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.evidence_item_id}>
                    <td>{i.source_type}<div className="muted">{i.source_ref} - {i.source_system}</div></td>
                    <td>v{i.version_no}{i.change_reason ? <div className="muted">{i.change_reason}</div> : null}
                      <div className="muted">sha256 {i.content_hash.slice(0, 12)}</div></td>
                    <td>{i.freshness} / {i.completeness}</td>
                    <td><When iso={i.as_of} /></td>
                    <td><details><summary>View</summary><Json value={i.content} /></details></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}
      <h2>Immutable snapshots</h2>
      <form action={freezeSnapshot} className="inline">
        <input type="hidden" name="case_id" value={id} />
        <SubmitButton>Freeze current evidence</SubmitButton>
      </form>
      {snaps.data && snaps.data.items.length > 0 ? (
        <table>
          <thead><tr><th>Version</th><th>Completeness</th><th>Open conflicts</th><th>Gaps</th><th>Created</th></tr></thead>
          <tbody>
            {snaps.data.items.map((s) => (
              <tr key={s.snapshot_id}>
                <td>v{s.version_no} <span className="muted">{s.snapshot_id.slice(0, 8)}</span></td>
                <td><span className={`badge badge-${s.completeness_status.toLowerCase()}`}>{s.completeness_status}</span></td>
                <td>{s.open_conflicts}</td>
                <td>{s.gaps.length}</td>
                <td><When iso={s.created_at} /> by {s.created_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <Empty>No snapshot yet.</Empty>}
    </section>
  );
}
