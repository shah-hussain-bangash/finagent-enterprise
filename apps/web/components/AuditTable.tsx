import { Empty, When } from "@/components/ui";

export interface AuditEvent {
  seq: number;
  occurred_at: string;
  action: string;
  object_type: string;
  object_id: string | null;
  outcome: string;
  actor_kind: string;
  actor_principal_id: string | null;
  case_id: string | null;
  hash?: string;
}

export function AuditTable({ items }: { items: AuditEvent[] }) {
  if (items.length === 0) return <Empty>No audit events visible to you.</Empty>;
  return (
    <table>
      <caption>Append-only, hash-chained audit events (metadata only)</caption>
      <thead><tr><th>#</th><th>When</th><th>Action</th><th>Object</th><th>Outcome</th><th>Actor</th></tr></thead>
      <tbody>
        {items.map((e) => (
          <tr key={e.seq}>
            <td>{e.seq}</td>
            <td><When iso={e.occurred_at} /></td>
            <td>{e.action}</td>
            <td>{e.object_type} {e.object_id ? <span className="muted">{e.object_id.slice(0, 8)}</span> : null}</td>
            <td>{e.outcome}</td>
            <td>{e.actor_kind} {e.actor_principal_id ? <span className="muted">{e.actor_principal_id.slice(0, 8)}</span> : null}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
