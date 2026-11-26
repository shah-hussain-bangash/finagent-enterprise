import Link from "next/link";

export function CasePageHeading({ caseId, title, description }: { caseId: string; title: string; description: string }) {
  return (
    <header className="case-page-heading">
      <p className="portal-eyebrow">INVESTIGATION / CASE {caseId.slice(0, 8)}</p>
      <h1 id="h">{title}</h1>
      <p>{description}</p>
      <Link href={`/cases/${caseId}`}>← Case overview</Link>
    </header>
  );
}
