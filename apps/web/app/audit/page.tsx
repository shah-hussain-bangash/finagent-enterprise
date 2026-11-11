import Link from "next/link";
import { AuditTable, type AuditEvent } from "@/components/AuditTable";
import { ErrorState } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

export default async function Audit({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  await requireSession();
  const sp = await searchParams;
  const after = Number(sp.after ?? "0") || 0;
  const q = new URLSearchParams({ after_seq: String(after), limit: "100" });
  if (sp.case_id) q.set("case_id", sp.case_id);
  const { data, error } = await load<{ items: AuditEvent[]; next_after_seq: number | null }>(`/api/v1/audit-events?${q}`);
  return (
    <section aria-labelledby="h">
      <h1 id="h">Audit history</h1>
      <p className="muted">Auditor view: tenant-wide, metadata only (DEC-15). People working a case see its activity on the case.</p>
      <form method="get" className="inline">
        <label htmlFor="case_id">Case</label>
        <input id="case_id" name="case_id" defaultValue={sp.case_id ?? ""} />
        <button type="submit">Filter</button>
      </form>
      {error ? <ErrorState error={error} what="Audit history" /> : null}
      {data ? <AuditTable items={data.items} /> : null}
      {data?.next_after_seq ? (
        <p><Link href={`/audit?${new URLSearchParams({ ...(sp.case_id ? { case_id: sp.case_id } : {}), after: String(data.next_after_seq) })}`}>Next page</Link></p>
      ) : null}
    </section>
  );
}
