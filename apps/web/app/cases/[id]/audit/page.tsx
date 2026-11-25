import { AuditTable, type AuditEvent } from "@/components/AuditTable";
import { ErrorState, Tabs } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

export default async function CaseAudit({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  await requireSession(`/cases/${id}/audit`);
  const { data, error } = await load<{ items: AuditEvent[] }>(`/api/v1/cases/${id}/activity`);
  return (
    <section aria-labelledby="h">
      <h1 id="h">Case audit history</h1>
      <Tabs caseId={id} active="/audit" />
      {error ? <ErrorState error={error} what="Audit history" /> : null}
      {data ? <AuditTable items={data.items} /> : null}
    </section>
  );
}
