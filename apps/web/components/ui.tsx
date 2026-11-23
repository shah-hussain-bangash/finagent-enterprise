// Shared, accessible UI pieces. Server components unless marked otherwise.
import Link from "next/link";
import type { ReactNode } from "react";
import type { ApiRequestError } from "@/lib/api";

export function Flash({ ok, error }: { ok?: string; error?: string }) {
  if (error) {
    return (
      <p role="alert" className="flash flash-error">
        <strong>Action not confirmed.</strong> {error}
      </p>
    );
  }
  if (ok) {
    return (
      <p role="status" className="flash flash-ok">
        <strong>Saved.</strong> {ok}
      </p>
    );
  }
  return null;
}

export function ErrorState({ error, what }: { error: ApiRequestError; what: string }) {
  const notFound = error.status === 404;
  const forbidden = error.status === 403 || error.code === "MFA_REQUIRED";
  return (
    <section role="alert" className="panel error-state">
      <h2>{notFound ? `${what} not found` : forbidden ? "Not permitted" : `Could not load ${what.toLowerCase()}`}</h2>
      <p>
        {notFound
          ? "It does not exist or you do not have access to it."
          : forbidden
            ? error.message
            : error.code === "CONNECTION_ERROR" ? error.message : `${error.message} (status ${error.status})`}
      </p>
      {typeof error.body?.error?.correlation_id === "string" && error.body.error.correlation_id ? <p className="muted">Correlation ID: {error.body.error.correlation_id}</p> : null}
      {error.code === "MFA_REQUIRED" ? <p><Link href="/security">Open security verification</Link></p> : null}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function When({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className="muted">-</span>;
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return <span className="muted">Invalid date</span>;
  return (
    <time dateTime={iso} title={iso}>
      {d.toISOString().replace("T", " ").slice(0, 19)} UTC
    </time>
  );
}

export function Tabs({ caseId, active }: { caseId: string; active: string }) {
  const tabs = [
    ["", "Overview"],
    ["/evidence", "Evidence"],
    ["/documents", "Documents"],
    ["/agents", "Agent findings"],
    ["/report", "Report"],
    ["/audit", "Audit"],
  ];
  return (
    <nav aria-label="Case sections" className="tabs">
      {tabs.map(([path, label]) => (
        <Link key={path} href={`/cases/${caseId}${path}`} aria-current={active === path ? "page" : undefined}>
          {label}
        </Link>
      ))}
      <Link href={`/workspace/case-overview?${new URLSearchParams({ case: caseId })}`}>Investigation overview</Link>
    </nav>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json" role="region" aria-label="Structured details" tabIndex={0}>{JSON.stringify(value, null, 2)}</pre>;
}
