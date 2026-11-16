"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";

type View =
  | "Dashboard"
  | "Cases"
  | "Documents"
  | "Approvals"
  | "Transactions"
  | "Investigation assistant"
  | "Agent monitor"
  | "Policies & evaluations"
  | "Supervisor"
  | "Security";
type Role = "Analyst" | "Reviewer" | "Supervisor";
const navigation: { title: string; items: View[] }[] = [
  {
    title: "INVESTIGATION",
    items: [
      "Dashboard",
      "Cases",
      "Documents",
      "Transactions",
      "Investigation assistant",
    ],
  },
  {
    title: "OVERSIGHT",
    items: [
      "Approvals",
      "Agent monitor",
      "Policies & evaluations",
      "Supervisor",
      "Security",
    ],
  },
];
const icons: Record<string, string> = {
  Dashboard: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  Cases: "M3 7h7l2-3h8v16H3z M3 10h17",
  Documents: "M6 3h9l4 4v14H6z M14 3v6h5 M9 13h7 M9 17h5",
  Approvals: "M9 4H5v17h15V4h-4 M9 3h7v4H9z M8 14l3 3 5-6",
  Transactions: "M3 7h17l-4-4 M21 17H4l4 4",
  "Investigation assistant": "M12 3l2 6 6 3-6 2-2 7-2-7-7-2 7-3z",
  "Agent monitor": "M3 12h4l3-8 4 16 3-8h4",
  "Policies & evaluations": "M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5",
  Supervisor:
    "M7 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M1 21v-3a6 6 0 0 1 12 0v3 M17 5a3 3 0 0 1 0 6 M17 15a5 5 0 0 1 5 5",
  Security: "M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6z M8 12l3 3 5-6",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6",
  bell: "M5 17h14l-2-3V9a5 5 0 0 0-10 0v5z M10 21h4",
  arrow: "M4 12h16 M15 7l5 5-5 5",
  menu: "M3 6h18 M3 12h18 M3 18h18",
  check: "M4 12l5 5L20 6",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v6l4 2",
};
function Icon({ name }: { name: string }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={icons[name] ?? icons.Documents} />
    </svg>
  );
}
function Badge({
  children,
  tone = "blue",
}: {
  children: ReactNode;
  tone?: "blue" | "green" | "amber" | "red" | "slate";
}) {
  return <span className={`ws-badge ws-${tone}`}>{children}</span>;
}
function Card({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="ws-card">
      <div className="ws-card-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
const cases = [
  {
    id: "DEMO-001",
    customer: "Aster Trading LLC",
    type: "Transaction monitoring",
    status: "Awaiting review",
    risk: "High",
    owner: "Maya Hassan",
    due: "Today",
    progress: 83,
  },
  {
    id: "DEMO-002",
    customer: "Noor Logistics",
    type: "Customer due diligence",
    status: "Investigating",
    risk: "Medium",
    owner: "Omar Ali",
    due: "Tomorrow",
    progress: 50,
  },
  {
    id: "DEMO-003",
    customer: "Cedar Ventures",
    type: "Document discrepancy",
    status: "Rework",
    risk: "High",
    owner: "Maya Hassan",
    due: "Overdue",
    progress: 66,
  },
  {
    id: "DEMO-004",
    customer: "Harbour Services",
    type: "Periodic KYC refresh",
    status: "Assigned",
    risk: "Low",
    owner: "Lina Ahmed",
    due: "In 3 days",
    progress: 17,
  },
];
const documents = [
  {
    name: "Proof of address",
    file: "proof-of-address-clean.pdf",
    state: "Selected",
    tone: "green" as const,
    source: "Analyst upload · v2",
    note: "Readable document with corrected fields. Selection reason is illustrative; backend selection metadata is not connected.",
    fields: [
      ["Full name", "SYNTHETIC Person 001", "Analyst correction · unverified"],
      [
        "Address",
        "001 Synthetic Street, Dubai, UAE",
        "Analyst correction · unverified",
      ],
      ["Issue date", "2026-11-10", "Analyst correction · date valid"],
    ],
  },
  {
    name: "Identity document",
    file: "synthetic-passport.pdf",
    state: "Parsed",
    tone: "blue" as const,
    source: "Mock connector · v1",
    note: "Synthetic identity document from the mock bank. Authenticity is not verified.",
    fields: [
      ["Full name", "SYNTHETIC Person 001", "Extracted · unverified"],
      ["Document number", "DEMO-P0001", "Extracted · unverified"],
      ["Expiry date", "2036-09-28", "Extracted · date valid"],
    ],
  },
  {
    name: "Earlier proof of address",
    file: "proof-of-address-original.pdf",
    state: "Quarantined",
    tone: "red" as const,
    source: "Analyst upload · v1",
    note: "Historical upload retained for audit. Its file content is unavailable in this preview and must not be opened or released.",
    fields: [
      ["Processing status", "Quarantined", "Active content detected"],
      ["Extraction", "Not available", "Retained historical warning"],
    ],
  },
];
const transactions = [
  {
    id: "TX-1001",
    date: "2026-11-01",
    name: "Aster settlement",
    direction: "Credit",
    amount: 12500,
    currency: "AED",
    status: "Posted",
  },
  {
    id: "TX-1002",
    date: "2026-11-02",
    name: "Harbour supplier",
    direction: "Debit",
    amount: 8400,
    currency: "AED",
    status: "Posted",
  },
  {
    id: "TX-1003",
    date: "2026-11-03",
    name: "Aster settlement",
    direction: "Credit",
    amount: 17941.39,
    currency: "AED",
    status: "Posted",
  },
  {
    id: "TX-1004",
    date: "2026-11-04",
    name: "Cedar services",
    direction: "Debit",
    amount: 12000,
    currency: "AED",
    status: "Pending",
  },
];
const money = (n: number) =>
  n.toLocaleString("en", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const reportSections = [
  "Summary",
  "KYC review",
  "Evidence gaps",
  "Version comparison",
];

export default function Workspace() {
  const [view, setView] = useState<View>("Dashboard");
  const [role, setRole] = useState<Role>("Analyst");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("All statuses");
  const [selectedCase, setSelectedCase] = useState(cases[0]!);
  const [documentIndex, setDocumentIndex] = useState(0);
  const [reportTab, setReportTab] = useState("Summary");
  const [decision, setDecision] = useState("");
  const [rationale, setRationale] = useState("");
  const [notice, setNotice] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [notifications, setNotifications] = useState(false);
  const [txDirection, setTxDirection] = useState("All directions");
  const [selectedTx, setSelectedTx] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [policy, setPolicy] = useState("KYC refresh");
  const [draftOwner, setDraftOwner] = useState("Maya Hassan");
  const [draftDue, setDraftDue] = useState("");
  const open = (next: View) => {
    setView(next);
    setSearch("");
    setNotice("");
    setMobileNav(false);
  };
  const filteredCases = cases.filter(
    (c) =>
      `${c.id} ${c.customer} ${c.type} ${c.owner}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (status === "All statuses" || c.status === status),
  );
  const filteredTx = transactions.filter(
    (t) =>
      `${t.id} ${t.name} ${t.date}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (txDirection === "All directions" || t.direction === txDirection),
  );
  const doc = documents[documentIndex]!;
  const caseTable = (
    <div className="ws-table-scroll">
      <table>
        <thead>
          <tr>
            <th>Case / customer</th>
            <th>Risk</th>
            <th>Status</th>
            <th>Assigned to</th>
            <th>Due</th>
            <th>
              <span className="ws-sr">Open case</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {filteredCases.map((c) => (
            <tr key={c.id}>
              <td>
                <button
                  className="ws-text-button"
                  onClick={() => {
                    setSelectedCase(c);
                    open("Cases");
                  }}
                >
                  {c.id}
                </button>
                <span className="ws-cell-sub">{c.customer}</span>
              </td>
              <td>
                <Badge
                  tone={
                    c.risk === "High"
                      ? "red"
                      : c.risk === "Medium"
                        ? "amber"
                        : "green"
                  }
                >
                  {c.risk}
                </Badge>
              </td>
              <td>
                <Badge tone={c.status === "Rework" ? "red" : "blue"}>
                  {c.status}
                </Badge>
              </td>
              <td>{c.owner}</td>
              <td className={c.due === "Overdue" ? "ws-danger" : ""}>
                {c.due}
              </td>
              <td>
                <button
                  className="ws-icon-button"
                  aria-label={`Open ${c.id}`}
                  onClick={() => {
                    setSelectedCase(c);
                    open("Cases");
                  }}
                >
                  <Icon name="arrow" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!filteredCases.length && (
        <p className="ws-empty">
          No sample cases match your search and status filter.
        </p>
      )}
    </div>
  );
  const metrics = (
    <div className="ws-metrics">
      {[
        [
          "Active cases",
          String(cases.length),
          "Within the sample portfolio",
          "Cases",
        ],
        ["Awaiting review", "1", "One draft needs a reviewer", "Approvals"],
        [
          "Evidence exceptions",
          "2",
          "One quarantine · one rework case",
          "Documents",
        ],
        [
          "Completed stages",
          "5 / 6",
          "Illustrative investigation run",
          "Agent monitor",
        ],
      ].map(([label, value, detail, target]) => (
        <button
          key={label}
          className="ws-metric"
          onClick={() => open(target as View)}
        >
          <span className="ws-metric-top">
            {label}
            <Icon name={target!} />
          </span>
          <strong>{value}</strong>
          <span>{detail}</span>
        </button>
      ))}
    </div>
  );
  const report = (
    <>
      <div className="ws-tabs" aria-label="Report sections">
        {reportSections.map((t) => (
          <button
            key={t}
            aria-pressed={reportTab === t}
            onClick={() => setReportTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="ws-report">
        <Badge tone="amber">Draft · not approved</Badge>
        {reportTab === "Summary" && (
          <>
            <h3>Review the evidence, then decide.</h3>
            <p>
              The sample case contains synthetic transaction and customer
              evidence. A corrected proof of address is available, while
              historical failed uploads remain in the evidence snapshot.
            </p>
            <div className="ws-callout">
              No conclusion about financial crime is made. An analyst and
              independent reviewer must assess the evidence.
            </div>
          </>
        )}
        {reportTab === "KYC review" && (
          <>
            <h3>Proof of address is present</h3>
            <p>
              Corrected issue date: <strong>2026-11-10</strong>. The name and
              address remain unverified analyst corrections.
            </p>
            <button
              className="ws-secondary"
              onClick={() => {
                setDocumentIndex(0);
                open("Documents");
              }}
            >
              Inspect supporting document <Icon name="arrow" />
            </button>
          </>
        )}
        {reportTab === "Evidence gaps" && (
          <>
            <h3>Exceptions need context</h3>
            <p>
              A historical upload is quarantined. A misclassified earlier upload
              has missing passport fields. These warnings are retained and must
              be evaluated separately from the selected identity document.
            </p>
            <button
              className="ws-secondary"
              onClick={() => {
                setDocumentIndex(2);
                open("Documents");
              }}
            >
              Inspect historical warning
            </button>
          </>
        )}
        {reportTab === "Version comparison" && (
          <>
            <h3>Version 2 → version 3</h3>
            <div className="ws-diff">
              <span>Previous: proof-of-address issue date missing.</span>
              <strong>
                Current: corrected issue date included in KYC review.
              </strong>
            </div>
            <p>
              Historical warnings remain visible. This is a sample comparison,
              not a live report diff.
            </p>
          </>
        )}
      </div>
    </>
  );
  const assistantReply = (text: string) => {
    setQuestion(text);
    if (!/transaction|proof|address|document/i.test(text)) {
      setAnswer(
        "This interface has two scripted examples only. Choose a document or transaction question above. No live AI response is available in this preview.",
      );
      return;
    }
    setAnswer(
      text.toLowerCase().includes("transaction")
        ? "The sample ledger includes three posted transactions and one pending transaction. Posted credits total AED 30,441.39; posted debits total AED 8,400.00. Pending transactions are excluded. These values describe this preview dataset only."
        : "The sample selected proof of address has a corrected issue date of 2026-11-10. Its name and address remain unverified. The earlier quarantined upload is retained separately. These are sample evidence facts, not an authenticity assessment.",
    );
  };

  return (
    <div className="fa-workspace">
      <aside
        className={`ws-sidebar ${mobileNav ? "ws-sidebar-open" : ""}`}
        aria-label="Workspace navigation"
      >
        <Link href="/workspace" className="ws-brand">
          <span className="ws-brand-mark">
            <Icon name="Security" />
          </span>
          <span>
            FinAgent<small>ENTERPRISE WORKSPACE</small>
          </span>
        </Link>
        <div className="ws-tenant">
          <span className="ws-avatar">SB</span>
          <div>
            <strong>Synthetic Bank</strong>
            <small>Interface design preview</small>
          </div>
          <Badge tone="slate">UI</Badge>
        </div>
        {navigation.map((group) => (
          <div className="ws-nav-group" key={group.title}>
            <p>{group.title}</p>
            {group.items.map((item) => (
              <button
                key={item}
                aria-current={view === item ? "page" : undefined}
                onClick={() => open(item)}
              >
                <Icon name={item} />
                <span>{item}</span>
                {item === "Approvals" && <small>1</small>}
              </button>
            ))}
          </div>
        ))}
        <div className="ws-sidebar-bottom">
          <span className="ws-status-dot" /> Local interface preview
          <p>Sample data · no production actions</p>
          <Link href="/">
            Open existing portal <Icon name="arrow" />
          </Link>
        </div>
      </aside>
      <div className="ws-main">
        <header className="ws-topbar">
          <button
            className="ws-icon-button ws-menu"
            aria-label="Toggle navigation"
            aria-expanded={mobileNav}
            onClick={() => setMobileNav(!mobileNav)}
          >
            <Icon name="menu" />
          </button>
          <span className="ws-breadcrumb">
            Workspace <span>/</span> <strong>{view}</strong>
          </span>
          <div className="ws-top-actions">
            <button
              className="ws-icon-button"
              aria-label="Sample notifications"
              aria-expanded={notifications}
              onClick={() => setNotifications(!notifications)}
            >
              <Icon name="bell" />
              <i className="ws-notification-dot" />
            </button>
            <span className="ws-avatar ws-avatar-blue">UI</span>
            <label className="ws-role">
              Preview role
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
              >
                <option>Analyst</option>
                <option>Reviewer</option>
                <option>Supervisor</option>
              </select>
            </label>
          </div>
          {notifications && (
            <div className="ws-popover">
              <strong>Sample notifications</strong>
              <p>DEMO-001 is ready for independent review.</p>
              <p>DEMO-003 needs rework.</p>
              <button
                className="ws-text-button"
                onClick={() => {
                  open("Approvals");
                  setNotifications(false);
                }}
              >
                Open review workspace
              </button>
            </div>
          )}
        </header>
        <div className="ws-preview-banner">
          <Badge tone="amber">INTERFACE PREVIEW</Badge>
          <span>
            All records and metrics below are sample data. Role selection
            changes the preview only. No decisions, uploads, assignments, or AI
            calls are sent to the backend.
          </span>
        </div>
        <div className="ws-content">
          <div className="ws-page-heading">
            <div>
              <p className="ws-eyebrow">FINAGENT / {view.toUpperCase()}</p>
              <h1>
                {view === "Dashboard"
                  ? `Your ${role.toLowerCase()} workspace`
                  : view}
              </h1>
              <p>
                {view === "Dashboard"
                  ? "Clear priorities. Connected evidence. Human decisions."
                  : "Explore the interface before backend integration and acceptance testing."}
              </p>
            </div>
            <span className="ws-environment">
              <span className="ws-status-dot" /> Synthetic environment
            </span>
          </div>
          {notice && (
            <div className="ws-notice" role="status">
              {notice}
              <button
                aria-label="Dismiss message"
                onClick={() => setNotice("")}
              >
                ×
              </button>
            </div>
          )}
          {view === "Dashboard" && (
            <>
              <div className="ws-hero">
                <div>
                  <Badge tone="blue">EVIDENCE FIRST</Badge>
                  <h2>
                    Every investigation.
                    <br />A clearer next step.
                  </h2>
                  <p>
                    {role === "Reviewer"
                      ? "Inspect report changes, resolve evidence exceptions, and prepare an independent decision."
                      : role === "Supervisor"
                        ? "See workload, overdue cases, and operational exceptions across your sample team."
                        : "Move from an alert to a traceable investigation, with evidence always in view."}
                  </p>
                  <button
                    className="ws-primary"
                    onClick={() =>
                      open(
                        role === "Reviewer"
                          ? "Approvals"
                          : role === "Supervisor"
                            ? "Supervisor"
                            : "Cases",
                      )
                    }
                  >
                    Open my workspace <Icon name="arrow" />
                  </button>
                </div>
                <div className="ws-hero-visual">
                  <span className="ws-orbit">
                    <Icon name="Security" />
                  </span>
                  <div>
                    <span>Evidence gathered</span>
                    <strong>Human review required</strong>
                    <small>Investigate → Explain → Review</small>
                  </div>
                </div>
              </div>
              {metrics}
              <div className="ws-columns">
                <Card
                  title="Investigation queue"
                  subtitle="Sample portfolio · select a case to inspect"
                  action={
                    <button
                      className="ws-text-button"
                      onClick={() => open("Cases")}
                    >
                      View all <Icon name="arrow" />
                    </button>
                  }
                >
                  {caseTable}
                </Card>
                <Card
                  title="Next actions"
                  subtitle="Priorities in this sample workspace"
                >
                  <div className="ws-action-list">
                    {[
                      [
                        "Review updated evidence",
                        "DEMO-001 · report v3",
                        "Approvals",
                      ],
                      [
                        "Explain historical warnings",
                        "DEMO-003 · analyst rework",
                        "Documents",
                      ],
                      [
                        "Check verification readiness",
                        "Required before protected decisions",
                        "Security",
                      ],
                    ].map(([title, detail, target], i) => (
                      <button key={title} onClick={() => open(target as View)}>
                        <span className="ws-step-number">0{i + 1}</span>
                        <span>
                          <strong>{title}</strong>
                          <small>{detail}</small>
                        </span>
                        <Icon name="arrow" />
                      </button>
                    ))}
                  </div>
                </Card>
              </div>
              <div className="ws-columns">
                <Card
                  title="Sample weekly activity"
                  subtitle="Illustrative case events, not live throughput"
                >
                  <div
                    className="ws-bar-chart"
                    role="img"
                    aria-label="Sample activity: Monday 8, Tuesday 12, Wednesday 9, Thursday 15, Friday 11 events"
                  >
                    {[8, 12, 9, 15, 11].map((n, i) => (
                      <div key={i}>
                        <strong>{n}</strong>
                        <span style={{ height: `${n * 7}px` }} />
                        <small>{["Mon", "Tue", "Wed", "Thu", "Fri"][i]}</small>
                      </div>
                    ))}
                  </div>
                </Card>
                <Card
                  title="Investigation controls"
                  subtitle="Human oversight remains part of every workflow"
                >
                  <div className="ws-control-list">
                    <p>
                      <Icon name="check" /> Evidence sources remain traceable
                    </p>
                    <p>
                      <Icon name="check" /> Corrections retain the original
                      values
                    </p>
                    <p>
                      <Icon name="check" /> Reports require independent review
                    </p>
                    <p>
                      <Icon name="Security" /> Protected actions use existing
                      MFA
                    </p>
                  </div>
                  <Link className="ws-link" href="/security">
                    Open existing security verification →
                  </Link>
                </Card>
              </div>
            </>
          )}
          {view === "Cases" && (
            <>
              <div className="ws-toolbar">
                <label className="ws-search">
                  <Icon name="search" />
                  <input
                    aria-label="Search sample cases"
                    placeholder="Search case, customer, analyst…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <select
                  aria-label="Filter case status"
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  {[
                    "All statuses",
                    "Awaiting review",
                    "Investigating",
                    "Rework",
                    "Assigned",
                  ].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </div>
              <Card
                title="Case queue"
                subtitle={`${filteredCases.length} sample cases`}
              >
                {caseTable}
              </Card>
              <div className="ws-case-header">
                <div>
                  <p className="ws-eyebrow">SELECTED SAMPLE CASE</p>
                  <h2>
                    {selectedCase.id}{" "}
                    <Badge tone="amber">{selectedCase.status}</Badge>
                  </h2>
                  <p>
                    {selectedCase.customer} · {selectedCase.type}
                  </p>
                </div>
                <div>
                  <Badge tone={selectedCase.risk === "High" ? "red" : "blue"}>
                    {selectedCase.risk} priority
                  </Badge>
                  <p>Owner: {selectedCase.owner}</p>
                </div>
              </div>
              <div
                className="ws-progress"
                aria-label={`${selectedCase.progress}% illustrative progress`}
              >
                <span style={{ width: `${selectedCase.progress}%` }} />
              </div>
              <div className="ws-stage-list">
                {[
                  "Intake",
                  "Evidence",
                  "Analysis",
                  "Report",
                  "Review",
                  "Complete",
                ].map((x, i) => (
                  <span
                    key={x}
                    className={
                      i < Math.floor((selectedCase.progress / 100) * 6)
                        ? "ws-stage-done"
                        : ""
                    }
                  >
                    {i + 1}. {x}
                  </span>
                ))}
              </div>
              <div className="ws-columns">
                <Card
                  title="Investigation brief"
                  subtitle="Case context and reviewer handoff"
                >
                  {report}
                </Card>
                <Card
                  title="Evidence & next action"
                  subtitle="Keep supporting records alongside the report"
                >
                  <div className="ws-action-list">
                    {documents.map((d, i) => (
                      <button
                        key={d.file}
                        onClick={() => {
                          setDocumentIndex(i);
                          open("Documents");
                        }}
                      >
                        <Icon name="Documents" />
                        <span>
                          <strong>{d.name}</strong>
                          <small>
                            {d.state} · {d.source}
                          </small>
                        </span>
                        <Icon name="arrow" />
                      </button>
                    ))}
                  </div>
                  <div className="ws-callout">
                    Next action:{" "}
                    {selectedCase.status === "Awaiting review"
                      ? "inspect the draft and evidence before preparing a review."
                      : "inspect case evidence and address outstanding exceptions."}
                  </div>
                  <button
                    className="ws-primary"
                    onClick={() => open("Investigation assistant")}
                  >
                    Explore case assistant <Icon name="arrow" />
                  </button>
                </Card>
              </div>
            </>
          )}
          {view === "Documents" && (
            <>
              <div className="ws-section-note">
                <Icon name="Documents" />
                <span>
                  Document comparison · DEMO-001 · sample evidence snapshot v3
                </span>
                <Badge tone="slate">3 records</Badge>
              </div>
              <div className="ws-doc-selector">
                {documents.map((d, i) => (
                  <button
                    key={d.file}
                    aria-pressed={documentIndex === i}
                    onClick={() => setDocumentIndex(i)}
                  >
                    <Icon name="Documents" />
                    <strong>{d.name}</strong>
                    <Badge tone={d.tone}>{d.state}</Badge>
                  </button>
                ))}
              </div>
              <div className="ws-equal-columns">
                <Card title="Document reading pane" subtitle={doc.file}>
                  <div className="ws-paper">
                    <div className="ws-watermark">SYNTHETIC · SAMPLE</div>
                    <p className="ws-eyebrow">ILLUSTRATIVE DOCUMENT CONTENT</p>
                    <h3>{doc.name}</h3>
                    {doc.state !== "Quarantined" ? (
                      <>
                        <p>
                          This reading pane is an HTML illustration. It is not
                          the original PDF or a verified document.
                        </p>
                        {doc.fields.map(([name, value]) => (
                          <div className="ws-paper-field" key={name}>
                            <small>{name}</small>
                            <mark>{value}</mark>
                          </div>
                        ))}
                        <p className="ws-paper-footer">
                          Page 1 · document preview integration pending
                        </p>
                      </>
                    ) : (
                      <div className="ws-callout ws-callout-red">
                        <Icon name="Security" />
                        <p>
                          Quarantined content is not rendered. Retain the record
                          and use the existing authorized review process.
                        </p>
                      </div>
                    )}
                  </div>
                </Card>
                <Card
                  title="Extracted fields & provenance"
                  subtitle={doc.source}
                  action={<Badge tone={doc.tone}>{doc.state}</Badge>}
                >
                  <p className="ws-body-copy">{doc.note}</p>
                  {doc.fields.map(([name, value, source]) => (
                    <div className="ws-field-card" key={name}>
                      <small>{name}</small>
                      <strong>{value}</strong>
                      <span>{source}</span>
                      <span className="ws-field-source">
                        Source: sample document · page 1
                      </span>
                    </div>
                  ))}
                  <div className="ws-callout">
                    Corrections preserve original values. Extraction and analyst
                    correction do not establish document authenticity.
                  </div>
                  <Link className="ws-link" href="/cases">
                    Open existing portal case queue →
                  </Link>
                </Card>
              </div>
            </>
          )}
          {view === "Approvals" && (
            <>
              <div className="ws-section-note">
                <Icon name="Approvals" />
                <span>Independent review · DEMO-001 · report v3</span>
                <Badge tone="amber">Sample pending request</Badge>
              </div>
              <div className="ws-columns">
                <Card
                  title="Frozen report preview"
                  subtitle="Compare the report with its supporting evidence"
                >
                  {report}
                </Card>
                <Card
                  title="Prepare a review"
                  subtitle="A local UI draft only; no decision will be recorded"
                >
                  <div className="ws-callout">
                    <strong>MFA readiness</strong>
                    <p>
                      Verification status is not connected in this preview. Use
                      the existing security page for real verification.
                    </p>
                    <Link href="/security">Open security verification →</Link>
                  </div>
                  <fieldset className="ws-decision">
                    <legend>Decision draft</legend>
                    {[
                      "Approve",
                      "Return for rework",
                      "Request information",
                      "Reject",
                    ].map((x) => (
                      <label key={x}>
                        <input
                          type="radio"
                          name="decision"
                          value={x}
                          checked={decision === x}
                          onChange={() => setDecision(x)}
                        />
                        {x}
                      </label>
                    ))}
                  </fieldset>
                  <label className="ws-form-label" htmlFor="review-rationale">
                    Rationale
                  </label>
                  <textarea
                    id="review-rationale"
                    value={rationale}
                    onChange={(e) => setRationale(e.target.value)}
                    placeholder="Explain your assessment and reference the supporting evidence…"
                  />
                  <p className="ws-fineprint">
                    This draft stays in this mounted preview while you switch
                    screens. Refreshing clears it. Opening security in another
                    tab preserves this page.
                  </p>
                  <button
                    className="ws-primary"
                    disabled={!decision || !rationale.trim()}
                    onClick={() =>
                      setNotice(
                        `Preview draft prepared: ${decision}. No approval was submitted or recorded.`,
                      )
                    }
                  >
                    Prepare decision draft <Icon name="arrow" />
                  </button>
                  <Link
                    className="ws-link"
                    href="/approvals"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open real approval center in a new tab →
                  </Link>
                </Card>
              </div>
            </>
          )}
          {view === "Transactions" && (
            <>
              <div className="ws-toolbar">
                <label className="ws-search">
                  <Icon name="search" />
                  <input
                    aria-label="Search sample transactions"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search transaction, counterparty, date…"
                  />
                </label>
                <select
                  aria-label="Filter transaction direction"
                  value={txDirection}
                  onChange={(e) => setTxDirection(e.target.value)}
                >
                  {["All directions", "Credit", "Debit"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </div>
              <div className="ws-metrics">
                {["Credit", "Debit"].map((d) => (
                  <div className="ws-metric" key={d}>
                    <span>Filtered posted {d.toLowerCase()}s · AED</span>
                    <strong>
                      {money(
                        filteredTx
                          .filter(
                            (t) => t.direction === d && t.status === "Posted",
                          )
                          .reduce((sum, t) => sum + t.amount, 0),
                      )}
                    </strong>
                    <span>Sample rows only · pending excluded</span>
                  </div>
                ))}
              </div>
              <Card
                title="Transaction ledger"
                subtitle="Select a row to inspect its source"
              >
                <div className="ws-table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Transaction</th>
                        <th>Date (UTC)</th>
                        <th>Counterparty</th>
                        <th>Direction</th>
                        <th>Amount</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTx.map((t) => (
                        <tr key={t.id}>
                          <td>
                            <button
                              className="ws-text-button"
                              onClick={() => setSelectedTx(t.id)}
                            >
                              {t.id}
                            </button>
                          </td>
                          <td>{t.date}</td>
                          <td>{t.name}</td>
                          <td>{t.direction}</td>
                          <td>AED {money(t.amount)}</td>
                          <td>
                            <Badge
                              tone={t.status === "Posted" ? "green" : "amber"}
                            >
                              {t.status}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!filteredTx.length && (
                    <p className="ws-empty">
                      No sample transactions match these filters.
                    </p>
                  )}
                </div>
              </Card>
              {selectedTx && (
                <Card
                  title={`Evidence detail · ${selectedTx}`}
                  subtitle="Illustrative source lineage"
                >
                  <p className="ws-body-copy">
                    Source: mock bank → transaction record → sample evidence
                    snapshot v3. Transaction links and graph expansion will be
                    connected during backend integration.
                  </p>
                  <button
                    className="ws-secondary"
                    onClick={() => setSelectedTx(null)}
                  >
                    Close detail
                  </button>
                </Card>
              )}
              <Card
                title="Counterparty relationships"
                subtitle="Illustrative relationships from the sample ledger"
              >
                <div className="ws-relationships">
                  <span>Aster settlement</span>
                  <Icon name="arrow" />
                  <strong>Sample account</strong>
                  <Icon name="arrow" />
                  <span>Harbour supplier</span>
                </div>
                <p className="ws-fineprint">
                  A relationship indicates a sample transfer connection, not
                  suspicious activity.
                </p>
              </Card>
            </>
          )}
          {view === "Investigation assistant" && (
            <div className="ws-columns">
              <Card
                title="Ask about the evidence"
                subtitle="Scripted UI demonstration · no model connected"
              >
                <div className="ws-assistant-welcome">
                  <span className="ws-assistant-icon">
                    <Icon name="Investigation assistant" />
                  </span>
                  <h3>
                    Start with a question.
                    <br />
                    Follow the evidence.
                  </h3>
                  <p>
                    Explore two sample answers and their sources. The assistant
                    cannot approve cases or take business actions.
                  </p>
                </div>
                <div className="ws-prompt-list">
                  {[
                    "What changed in the proof of address?",
                    "Summarize the posted transactions.",
                  ].map((q) => (
                    <button
                      className="ws-secondary"
                      key={q}
                      onClick={() => assistantReply(q)}
                    >
                      {q}
                      <Icon name="arrow" />
                    </button>
                  ))}
                </div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (question.trim()) assistantReply(question);
                  }}
                >
                  <label className="ws-form-label" htmlFor="assistant-question">
                    Question
                  </label>
                  <textarea
                    id="assistant-question"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="Ask about sample documents or transactions…"
                  />
                  <button className="ws-primary" disabled={!question.trim()}>
                    Show scripted sample answer
                  </button>
                </form>
                {answer && (
                  <div className="ws-answer">
                    <Badge tone="slate">Scripted answer · not live AI</Badge>
                    <p>{answer}</p>
                    <button
                      className="ws-text-button"
                      onClick={() =>
                        open(
                          question.toLowerCase().includes("transaction")
                            ? "Transactions"
                            : "Documents",
                        )
                      }
                    >
                      Inspect sample supporting evidence <Icon name="arrow" />
                    </button>
                  </div>
                )}
              </Card>
              <Card
                title="Evidence boundaries"
                subtitle="A helpful assistant should explain its limits"
              >
                <div className="ws-control-list">
                  <p>
                    <Icon name="Documents" /> Cite supporting evidence for
                    factual answers
                  </p>
                  <p>
                    <Icon name="Security" /> Respect the signed-in user’s access
                  </p>
                  <p>
                    <Icon name="check" /> Separate facts from interpretations
                  </p>
                  <p>
                    <Icon name="clock" /> Disclose stale or missing evidence
                  </p>
                </div>
                <div className="ws-callout">
                  Planned integration: evidence retrieval, authorized citations,
                  model gateway, and answer validation.
                </div>
              </Card>
            </div>
          )}
          {view === "Agent monitor" && (
            <>
              <div className="ws-section-note">
                <Icon name="Agent monitor" />
                <span>Illustrative execution trace · DEMO-001</span>
                <Badge tone="amber">Waiting for human review</Badge>
              </div>
              <div className="ws-metrics">
                {[
                  ["Completed steps", "5 / 6"],
                  ["Elapsed duration", "2.4 s"],
                  ["Retries", "0"],
                  ["Token usage / cost", "Not connected"],
                ].map(([l, v]) => (
                  <div className="ws-metric" key={l}>
                    <span>{l}</span>
                    <strong>{v}</strong>
                    <span>Sample trace</span>
                  </div>
                ))}
              </div>
              <Card
                title="Execution timeline"
                subtitle="Understand how a draft reaches the reviewer"
              >
                <ol className="ws-timeline">
                  {[
                    [
                      "Gather evidence",
                      "Mock customer profile, documents, and transactions",
                      "0.6 s",
                    ],
                    [
                      "Process documents",
                      "Scan, parse, and retain source provenance",
                      "0.8 s",
                    ],
                    [
                      "Analyze KYC / AML",
                      "Produce evidence-grounded draft findings",
                      "0.4 s",
                    ],
                    [
                      "Check policies",
                      "Attach applicable policy references",
                      "0.3 s",
                    ],
                    [
                      "Draft report",
                      "Keep gaps and limitations explicit",
                      "0.3 s",
                    ],
                    [
                      "Independent review",
                      "A human decision is required",
                      "Waiting",
                    ],
                  ].map(([title, detail, duration], i) => (
                    <li key={title}>
                      <span
                        className={`ws-timeline-node ${i === 5 ? "ws-node-wait" : ""}`}
                      >
                        {i === 5 ? (
                          <Icon name="clock" />
                        ) : (
                          <Icon name="check" />
                        )}
                      </span>
                      <div>
                        <strong>{title}</strong>
                        <p>{detail}</p>
                      </div>
                      <Badge tone={i === 5 ? "amber" : "green"}>
                        {duration}
                      </Badge>
                    </li>
                  ))}
                </ol>
              </Card>
              <Link className="ws-link" href="/operations">
                Open existing operations tools →
              </Link>
            </>
          )}
          {view === "Policies & evaluations" && (
            <>
              <div className="ws-tabs">
                {["KYC refresh", "AML structuring"].map((p) => (
                  <button
                    key={p}
                    aria-pressed={policy === p}
                    onClick={() => setPolicy(p)}
                  >
                    {p}
                  </button>
                ))}
              </div>
              <div className="ws-equal-columns">
                <Card
                  title={policy}
                  subtitle="Sample policy version comparison"
                >
                  <div className="ws-field-card">
                    <small>Policy version</small>
                    <strong>{policy === "KYC refresh" ? "1.3" : "2.0"}</strong>
                    <span>Sample reference · not a policy publication</span>
                  </div>
                  <h3>
                    {policy === "KYC refresh"
                      ? "Proof-of-address recency"
                      : "Transaction pattern indicators"}
                  </h3>
                  <p className="ws-body-copy">
                    {policy === "KYC refresh"
                      ? "The sample policy requires proof of address dated within 90 days of review. Validate its applicability and effective date in the real policy workspace."
                      : "Repeated deposits below a threshold may require analyst review. An indicator does not establish a determination."}
                  </p>
                  <Link className="ws-link" href="/policies">
                    Open existing policy search →
                  </Link>
                </Card>
                <Card
                  title="Evaluation readiness"
                  subtitle="Design the checks before promoting changes"
                >
                  <div className="ws-control-list">
                    <p>
                      <Icon name="check" /> Corrected readable document
                      selection
                    </p>
                    <p>
                      <Icon name="check" /> Quarantined content stays isolated
                    </p>
                    <p>
                      <Icon name="check" /> Future-dated evidence remains
                      flagged
                    </p>
                    <p>
                      <Icon name="check" /> Cross-customer evidence stays
                      excluded
                    </p>
                  </div>
                  <Badge tone="amber">
                    Scenario checklist · no evaluation run
                  </Badge>
                  <p className="ws-fineprint">
                    Results and promotion controls will be connected in the
                    testing phase.
                  </p>
                  <Link className="ws-link" href="/evaluations">
                    Open existing evaluations →
                  </Link>
                </Card>
              </div>
            </>
          )}
          {view === "Supervisor" && (
            <>
              {metrics}
              <div className="ws-columns">
                <Card
                  title="Team workload"
                  subtitle="Illustrative assignments across four sample cases"
                >
                  <div className="ws-workload">
                    {["Maya Hassan", "Omar Ali", "Lina Ahmed"].map((name) => {
                      const count = cases.filter(
                        (c) => c.owner === name,
                      ).length;
                      return (
                        <div key={name}>
                          <span>{name}</span>
                          <div>
                            <i style={{ width: `${(count / 2) * 100}%` }} />
                          </div>
                          <strong>{count} cases</strong>
                        </div>
                      );
                    })}
                  </div>
                  <div className="ws-callout ws-callout-red">
                    DEMO-003 is overdue in this sample portfolio. Review
                    ownership and the outstanding evidence questions.
                  </div>
                </Card>
                <Card
                  title="Assignment draft"
                  subtitle="Explore assignment controls without changing ownership"
                >
                  <label className="ws-form-label" htmlFor="draft-owner">
                    Sample case DEMO-003 · proposed analyst
                  </label>
                  <select
                    id="draft-owner"
                    value={draftOwner}
                    onChange={(e) => setDraftOwner(e.target.value)}
                  >
                    {["Maya Hassan", "Omar Ali", "Lina Ahmed"].map((n) => (
                      <option key={n}>{n}</option>
                    ))}
                  </select>
                  <label className="ws-form-label" htmlFor="draft-due">
                    Proposed due date
                  </label>
                  <input
                    id="draft-due"
                    type="date"
                    value={draftDue}
                    onChange={(e) => setDraftDue(e.target.value)}
                  />
                  <button
                    className="ws-primary"
                    disabled={!draftDue}
                    onClick={() =>
                      setNotice(
                        `Assignment draft: DEMO-003 → ${draftOwner}, due ${draftDue}. No assignment or deadline was changed.`,
                      )
                    }
                  >
                    Prepare assignment draft
                  </button>
                  <Link className="ws-link" href="/operations">
                    Open existing operations →
                  </Link>
                </Card>
              </div>
              <Card
                title="Case oversight"
                subtitle="Select a sample case to inspect its next action"
              >
                {caseTable}
              </Card>
            </>
          )}
          {view === "Security" && (
            <>
              <div className="ws-callout">
                <strong>Preview role is not authorization.</strong>
                <p>
                  Changing the role selector never changes account permissions
                  or MFA status. Existing portal routes enforce their own access
                  controls.
                </p>
              </div>
              <div className="ws-equal-columns">
                <Card
                  title="Security verification"
                  subtitle="Use your existing enrolled authenticator"
                >
                  <div className="ws-security-illustration">
                    <Icon name="Security" />
                  </div>
                  <Badge tone="slate">Live status not connected</Badge>
                  <p className="ws-body-copy">
                    Complete MFA in the existing portal before protected
                    actions. Never paste authenticator setup keys or
                    verification codes into an investigation report.
                  </p>
                  <Link
                    className="ws-primary"
                    href="/security"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open verification in a new tab <Icon name="arrow" />
                  </Link>
                </Card>
                <Card
                  title="Account & access"
                  subtitle="Connected controls and planned capabilities"
                >
                  <div className="ws-action-list">
                    <Link href="/security/passkeys">
                      <Icon name="Security" />
                      <span>
                        <strong>Passkey management</strong>
                        <small>Open the existing account page</small>
                      </span>
                      <Icon name="arrow" />
                    </Link>
                    <Link href="/security/email">
                      <Icon name="check" />
                      <span>
                        <strong>Email verification</strong>
                        <small>Open the existing verification flow</small>
                      </span>
                      <Icon name="arrow" />
                    </Link>
                  </div>
                  <div className="ws-field-card">
                    <small>Session inventory & access history</small>
                    <strong>Integration pending</strong>
                    <span>
                      No active-session or role-administration API is connected
                      to this preview.
                    </span>
                  </div>
                </Card>
              </div>
            </>
          )}
          <footer className="ws-footer">
            <span>FinAgent Enterprise · interface development</span>
            <span>
              SYNTHETIC DATA ONLY · Human review required · Backend integration
              pending
            </span>
          </footer>
        </div>
      </div>
    </div>
  );
}
