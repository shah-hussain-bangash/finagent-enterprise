"use client";

import { useActionState } from "react";
import { searchEvidence } from "./search-action";
import type { EvidenceSearchState } from "@/lib/evidence-search-types";

const initial: EvidenceSearchState = {};
export function EvidenceSearch({ caseId }: { caseId: string }) {
  const [state, action, pending] = useActionState(searchEvidence, initial);
  return <section className="panel"><h3>Search current case evidence</h3>
    <p>Literal text search only; no AI model is used. Results are matches, not findings. Open the original evidence to review full context.</p>
    <form className="evidence-search-form" action={action} aria-busy={pending}><input type="hidden" name="case_id" value={caseId} />
      <label htmlFor="evidence-query">Search text (3–80 characters)</label>{" "}<input id="evidence-query" name="query" required minLength={3} maxLength={80} />{" "}<button type="submit" disabled={pending}>{pending ? "Searching…" : "Search evidence"}</button>
    </form>
    {pending && <p role="status">Searching the current case evidence…</p>}
    {!pending && state.error && <p role="alert">{state.error}</p>}
    {!pending && state.sessionExpired && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(`/cases/${caseId}/evidence`)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to search again.</p>}
    {!pending && state.matches && <div aria-live="polite"><p>{state.matches.length} match(es). {state.limited ? "Search limits were reached; results may be incomplete." : "No completeness or risk conclusion is implied."}</p>
      {state.matches.length === 0 ? <p>No matching text was returned; this does not prove the information is absent.</p> : <ul>{state.matches.map((match, index) => <li key={`${match.evidence_ref}-${index}`}><strong>{match.source_type}</strong> · {match.source_ref} · {match.evidence_ref} · v{match.version_no} · {match.freshness} / {match.completeness}<p>Field: {match.field_path}. Context excerpt{match.excerpt_truncated ? " (truncated)" : ""}: <q>{match.excerpt}</q></p>{/^ev:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(match.evidence_ref) && <p><a href={`/cases/${caseId}/evidence#evidence-${match.evidence_ref.slice(3)}`}>Open source evidence →</a></p>}</li>)}</ul>}
    </div>}
  </section>;
}
