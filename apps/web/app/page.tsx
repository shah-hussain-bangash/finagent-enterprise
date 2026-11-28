import Link from "next/link";
import { CASE_STATUS_LABEL } from "@/components/StatusBadge";
import { ErrorState, When } from "@/components/ui";
import { listCases, load, type CaseStatus } from "@/lib/api";
import { requireSession } from "@/lib/session";
import { sessionIdentity } from "@/lib/session-identity";
import { CAPABILITIES, CAPABILITY_DETAILS } from "@/lib/capability-map";

interface Overview {
  generated_at: string;
  scope: string;
  cases_by_status?: Record<string, number>;
  pending_reviews?: number;
  open_escalations?: number;
  open_quarantine?: number;
  runs_by_status?: Record<string, number>;
  uncertain_operations?: number;
  open_dead_letters?: number;
  model_usage_month?: { requests: number; tokens: number; estimated_cost: string };
}

const CASE_STATUSES: CaseStatus[] = ["RECEIVED", "ASSIGNED", "INVESTIGATING", "BLOCKED", "AWAITING_REVIEW", "REWORK", "APPROVED", "FINALIZING", "COMPLETED", "CANCELLED"];
const IMPROVEMENT_PRIORITIES = [
  { title: "Match the portal and API", detail: "Run the updated services together so case filters and supervisor controls can work.", area: 18 },
  { title: "Complete one case journey", detail: "Connect case selection, evidence, documents, report submission and independent review.", area: 4 },
  { title: "Improve case assignment", detail: "Provide authorized searchable selection of eligible analysts and reviewers.", area: 3 },
  { title: "Focus the dashboard on work", detail: "Highlight accessible assignments, due dates and next actions while keeping planning separate.", area: 1 },
  { title: "Complete connected features", detail: "Prioritize evidence, documents and reports; keep unfinished assistant and bilingual screens labeled as previews.", area: 8 },
  { title: "Resolve GitHub checks", detail: "Verify and upload the local fixes for the reported automated-check failures.", area: 20 },
  { title: "Make startup reliable", detail: "Prepare a repeatable local startup for the matching portal, API and required services.", area: 19 },
];
const REMAINING_CAPABILITIES = [
  { title: "Real financial-data integrations", details: "Approved customer, account, transaction, alert, sanctions and document sources.", area: 18 },
  { title: "Complete case operations", details: "Searchable assignee selection, analyst notes, notifications and supervisor workflows.", area: 3 },
  { title: "Connected AI and bilingual assistance", details: "Evidence-grounded answers, citations, translation review and approved model controls.", area: 10 },
  { title: "Review and operational controls", details: "Complete reviewer and supervisor journeys, monitoring, audit and recovery tools.", area: 14 },
  { title: "Security and resilience", details: "Institution-approved identity, access, encryption, backup and disaster recovery.", area: 19 },
  { title: "Production testing and acceptance", details: "End-to-end, security, accessibility, performance and recovery testing, plus institutional approval.", area: 20 },
];

function Kpi({ label, value, href }: { label: string; value: number | string | undefined; href?: string }) {
  const content = <><span className="overview-kpi-label">{label}</span><span className="overview-kpi-value">{value ?? "Not available"}</span></>;
  return (
    <div className="overview-kpi">{href && value !== undefined ? <Link href={href} className="overview-kpi-content">{content}</Link> : <div className="overview-kpi-content">{content}</div>}</div>
  );
}

export default async function Home() {
  const session = await requireSession();
  const identity = await sessionIdentity(session);
  const canReadCases = identity?.permissions.includes("CASE_READ") ?? false;
  const canReview = identity?.permissions.includes("APPROVAL_DECIDE") ?? false;
  const canOpenPreview = canReadCases || canReview || (identity?.permissions.includes("PLATFORM_OPERATE") ?? false);
  const { data, error } = await load<Overview>("/api/v1/reporting/overview");
  const cases = canReadCases && data?.cases_by_status ? await load<Awaited<ReturnType<typeof listCases>>>("/api/v1/cases") : null;
  return (
    <section className="operational-overview" aria-labelledby="h">
      <div className="overview-header">
        <div className="overview-hero-copy">
          <p className="overview-eyebrow">FINAGENT ENTERPRISE / INVESTIGATION PORTAL</p>
          <h1 id="h">Investigate. Correlate. <span>Review.</span></h1>
          <p>Follow the evidence and move investigations forward with human oversight.</p>
          {canReadCases && <Link className="overview-hero-link" href="/cases">Open case queue <span aria-hidden="true">→</span></Link>}
        </div>
      </div>
      <nav className="overview-shortcuts" aria-label="Investigation shortcuts">
        {canReadCases && <Link href="/cases">Open case queue</Link>}
        {canReview && <Link href="/approvals">Open review requests</Link>}
        {canReadCases && <Link href="/escalations">Open escalations</Link>}
        {canOpenPreview && <Link href="/capabilities">View all capabilities</Link>}
      </nav>
      {error ? <ErrorState error={error} what="Overview" /> : null}
      {canOpenPreview && <section className="dashboard-improvements" aria-labelledby="improvement-heading">
        <div className="overview-worklist-header"><div><p className="portal-eyebrow">NEXT DEVELOPMENT STEPS</p><h2 id="improvement-heading">Improvement priorities</h2></div><Link href="/capabilities">View capability map →</Link></div>
        <p>Planned improvements for the current system. These items are not a claim of completed or production-ready functionality.</p>
        <ol>{IMPROVEMENT_PRIORITIES.map((item, index) => <li key={item.title}>
          <span className="dashboard-capability-number">{String(index + 1).padStart(2, "0")}</span><div><strong>{item.title}</strong><p>{item.detail}</p><Link href={`/capabilities#capability-${item.area}`}>View related scope →</Link></div>
        </li>)}</ol>
      </section>}
      {canOpenPreview && <section className="dashboard-remaining" aria-labelledby="remaining-capabilities-heading">
        <div className="overview-worklist-header"><div><p className="portal-eyebrow">PRODUCT DEVELOPMENT</p><h2 id="remaining-capabilities-heading">Remaining capabilities</h2></div><Link href="/capabilities">View all 20 areas →</Link></div>
        <p>These areas include unfinished work. Their presence here does not mean they are operational or approved for production.</p>
        <ul>{REMAINING_CAPABILITIES.map(item => <li key={item.title}><strong>{item.title}</strong><span>{item.details}</span><Link href={`/capabilities#capability-${item.area}`}>View scope →</Link></li>)}</ul>
        <p className="dashboard-remaining-note">Recent case-management features also need the updated API, and local changes require verification before release.</p>
      </section>}
      {canOpenPreview && <details className="dashboard-capability-map">
        <summary><span>Full capability scope</span><small>20 areas · open to explore</small></summary>
        <p>Target scope includes connected features, previews and work still to be implemented. This list is not a production-readiness claim.</p>
        <ol>{CAPABILITIES.map((item, index) => <li key={item.title}>
          <details><summary><span className="dashboard-capability-number">{String(index + 1).padStart(2, "0")}</span><strong>{item.title}</strong></summary>
            <p>{item.description}</p><ul>{CAPABILITY_DETAILS[index]?.map(detail => <li key={detail}>{detail}</li>)}</ul>
            <p><strong>Remaining:</strong> {item.remaining}</p><Link href={`/capabilities#capability-${index + 1}`}>View capability details →</Link>
          </details>
        </li>)}</ol>
      </details>}
      {data ? (
        <>
          <p className="overview-scope">Counts within your access scope ({data.scope}). Generated {data.generated_at}.</p>
          {data.cases_by_status ? (
            <section aria-labelledby="overview-cases"><h2 id="overview-cases">Cases and reviews</h2><div className="overview-grid overview-primary-grid">
              <Kpi label="Assigned cases" value={data.cases_by_status.ASSIGNED ?? 0} href={canReadCases ? "/cases?status=ASSIGNED" : undefined} />
              <Kpi label="pending reviews" value={data.pending_reviews} href={canReview ? "/approvals" : undefined} />
              <Kpi label="open escalations" value={data.open_escalations} href={canReadCases ? "/escalations" : undefined} />
              <Kpi label="Completed" value={data.cases_by_status.COMPLETED ?? 0} href={canReadCases ? "/cases?status=COMPLETED" : undefined} />
            </div>
              <details className="overview-status-details"><summary>View all case statuses</summary><div className="overview-grid">{CASE_STATUSES.map((s) => <Kpi key={s} label={CASE_STATUS_LABEL[s]} value={data.cases_by_status?.[s] ?? 0} href={canReadCases ? `/cases?status=${s}` : undefined} />)}</div></details>
              {typeof data.open_quarantine === "number" && <p className="overview-help">Documents in quarantine: <strong>{data.open_quarantine}</strong> in your access scope. A separate quarantine list is not available here; open a case to review its documents. {canReadCases && <Link href="/cases">Open case queue →</Link>}</p>}
            </section>
          ) : null}
          {cases && <section className="overview-worklist" aria-labelledby="overview-worklist-title">
            <div className="overview-worklist-header"><div><p className="portal-eyebrow">YOUR WORKLOAD</p><h2 id="overview-worklist-title">Recent cases</h2></div><Link href="/cases">View case queue →</Link></div>
            {cases.error ? <ErrorState error={cases.error} what="Recent cases" /> : cases.data.items.length === 0 ? <p>No cases were returned for your account.</p> : <div className="overview-worklist-table" role="region" aria-label="Recent cases" tabIndex={0}><table><caption className="visually-hidden">Recent cases in your access scope</caption><thead><tr><th scope="col">Case</th><th scope="col">Alert</th><th scope="col">Priority</th><th scope="col">Status</th><th scope="col">Updated</th></tr></thead><tbody>{cases.data.items.slice(0, 5).map(c => <tr key={c.case_id}><td><Link href={`/cases/${c.case_id}`}>{c.case_id.slice(0, 8)}</Link></td><td>{c.alert_type}</td><td>{c.priority}</td><td>{CASE_STATUS_LABEL[c.status] ?? c.status}</td><td><When iso={c.updated_at} /></td></tr>)}</tbody></table></div>}
          </section>}
          {canOpenPreview && <section className="overview-capabilities" aria-labelledby="capability-heading">
            <div className="overview-worklist-header"><div><p className="portal-eyebrow">EXPLORE FINAGENT</p><h2 id="capability-heading">Investigation capabilities</h2></div><Link href="/workspace">Open workspace preview →</Link></div>
            <p>Follow the investigation from case selection through evidence and report review. Connected views use records available to your account.</p>
            <ol className="capability-grid">
              {canReadCases && <>
                <li><Link className="capability-card" href="/cases"><span className="capability-type">01 · CONNECTED</span><strong>Choose a case</strong><span>Open the case queue and select an accessible investigation.</span></Link></li>
                <li><Link className="capability-card" href="/workspace/case-overview"><span className="capability-type">02 · CONNECTED · READ ONLY</span><strong>Review the case</strong><span>Inspect case status, evidence gaps and recorded indicators.</span></Link></li>
                <li><Link className="capability-card" href="/workspace/evidence-briefing"><span className="capability-type">03 · CONNECTED · READ ONLY</span><strong>Inspect evidence</strong><span>Review available sources and their recorded details.</span></Link></li>
                <li><Link className="capability-card" href="/workspace/recorded-transactions"><span className="capability-type">04 · CONNECTED · READ ONLY</span><strong>Review transactions</strong><span>Explore transaction evidence and filter recorded rows.</span></Link></li>
                <li><Link className="capability-card" href="/workspace/recorded-provenance"><span className="capability-type">05 · CONNECTED · READ ONLY</span><strong>Review documents</strong><span>Inspect document versions and extracted fields.</span></Link></li>
                <li><Link className="capability-card" href="/cases"><span className="capability-type">06 · CONNECTED</span><strong>Prepare the report</strong><span>Open a case and use its report tab to review or edit its draft.</span></Link></li>
                <li><Link className="capability-card" href="/workspace/recorded-review"><span className="capability-type">07 · CONNECTED · READ ONLY</span><strong>Review report history</strong><span>Compare recorded report versions and review information.</span></Link></li>
              </>}
              {canReview && <li><Link className="capability-card" href="/approvals"><span className="capability-type">REVIEWER · CONNECTED</span><strong>Independent review</strong><span>Open review requests available to your account.</span></Link></li>}
            </ol>
            <div className="capability-preview-panel"><div><span className="capability-type">INTERFACE PREVIEW</span><strong>Explore AI and oversight concepts</strong><p>These illustrative assistant, evaluation and monitoring screens do not perform case actions.</p></div><Link href="/workspace">Open preview →</Link></div>
          </section>}
          {data.runs_by_status ? (
            <>
              <h2>Operations</h2>
              <div className="overview-grid">
                {Object.entries(data.runs_by_status).map(([k, v]) => <Kpi key={k} label={`runs ${k.toLowerCase()}`} value={v} />)}
                <Kpi label="uncertain external operations" value={data.uncertain_operations} href="/operations" />
                <Kpi label="open dead letters" value={data.open_dead_letters} />
                {data.model_usage_month ? (
                  <Kpi label="model tokens this month (est. cost)"
                       value={`${data.model_usage_month.tokens} (${data.model_usage_month.estimated_cost})`} />
                ) : null}
              </div>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
