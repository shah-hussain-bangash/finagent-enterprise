import Link from "next/link";

/** Navigation only: each destination continues to enforce its own case access. */
export function ConnectedCaseNav({ caseId, active }: { caseId: string; active: string }) {
  const query = new URLSearchParams({ case: caseId });
  const views = [
    { key: "overview", label: "Overview", href: `/workspace/case-overview?${query}` },
    { key: "evidence", label: "Evidence", href: `/workspace/evidence-briefing?${query}` },
    { key: "transactions", label: "Transactions", href: `/workspace/recorded-transactions?${query}` },
    { key: "documents", label: "Document history", href: `/workspace/recorded-provenance?${query}` },
    { key: "risk", label: "Recorded indicators", href: `/workspace/recorded-risk?${query}` },
    { key: "report", label: "Report history", href: `/workspace/recorded-review?${query}` },
    { key: "runs", label: "Investigation runs", href: `/workspace/recorded-monitor?${query}` },
    { key: "workflow", label: "Case workflow", href: `/cases/${caseId}` },
  ];
  return <nav className="connected-case-nav" aria-label={`Investigation navigation for case ${caseId.slice(0, 8)}`}>
    <span>Case {caseId.slice(0, 8)}</span>
    <div>{views.map(view => <Link key={view.key} href={view.href} prefetch={false} aria-current={view.key === active ? "page" : undefined}>{view.label}</Link>)}</div>
  </nav>;
}
