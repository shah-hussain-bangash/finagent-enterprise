import { CasePageHeading } from "@/components/CasePageHeading";
import { AuditTable, type AuditEvent } from "@/components/AuditTable";
import { ErrorState, Tabs } from "@/components/ui";
import { load } from "@/lib/api";
import { requireSession } from "@/lib/session";

export default async function CaseAudit({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  await requireSession(`/cases/${id}/audit`);
  const { data, error } = await load<{ items: AuditEvent[] }>(`/api/v1/cases/${id}/activity`, `/cases/${id}/audit`);
  return (
    <section className="case-journey" aria-labelledby="h">
      <CasePageHeading caseId={id} title="Case audit history" description="Review recorded activity and decisions for this case." />
      <Tabs caseId={id} active="/audit" />
      {error ? <ErrorState error={error} what="Audit history" /> : null}
      {data ? <AuditTable items={data.items} /> : null}
    </section>
  );
}
