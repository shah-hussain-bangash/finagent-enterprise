"use client";

import { useRef, useState } from "react";
import { reassignCase, searchCaseAssignees } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { caseUpdatePath } from "@/lib/case-navigation";

export function CaseAssignmentForm({ caseId, recordVersion, analystId, reviewerId, queueHref }: {
  caseId: string; recordVersion: number; analystId: string | null; reviewerId: string | null; queueHref: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [role, setRole] = useState("ANALYST");
  const [query, setQuery] = useState("");
  const [personId, setPersonId] = useState("");
  const [people, setPeople] = useState<{id: string; display_name: string}[]>([]);
  const [searchMessage, setSearchMessage] = useState("");
  const [searching, setSearching] = useState(false);
  const searchGeneration = useRef(0);
  async function searchPeople() {
    const generation = ++searchGeneration.current;
    setPersonId("");
    setSearching(true);
    setSearchMessage("Searching…");
    setPeople([]);
    try {
      const result = await searchCaseAssignees(caseId, role, query);
      if (generation !== searchGeneration.current) return;
      setPeople(result.items);
      setSearchMessage(result.error ?? (result.items.length ? "Select a person from the results. Up to 20 matches are shown; refine your search if needed." : "No other eligible people found. Current case assignees are excluded."));
    } catch {
      if (generation === searchGeneration.current) setSearchMessage("Search is unavailable. Try again or enter a person ID.");
    } finally {
      if (generation === searchGeneration.current) setSearching(false);
    }
  }
  function requireReload(message: string) {
    setError(message);
    setUncertain(true);
    const warning = "CONNECTION_ERROR: The assignment outcome may be unknown; check the record before trying again.";
    window.location.assign(caseUpdatePath(caseId, queueHref, "error", warning));
  }
  async function submit(form: FormData) {
    const principalId = String(form.get("principal_id") ?? "").trim().toLowerCase();
    const otherAssignee = role === "ANALYST" ? reviewerId : analystId;
    const currentAssignee = role === "ANALYST" ? analystId : reviewerId;
    if (currentAssignee && principalId === currentAssignee.toLowerCase()) {
      setError("This person is already assigned to that role. Choose another person.");
      return;
    }
    if (otherAssignee && principalId === otherAssignee.toLowerCase()) {
      setError("The analyst and reviewer must be different people. Choose another person.");
      return;
    }
    try {
      const result = await reassignCase(form);
      if (result.ok) window.location.assign(caseUpdatePath(caseId, queueHref, "ok", result.message));
      else if (result.uncertain) requireReload(result.message);
      else if (result.message.startsWith("VERSION_CONFLICT:")) window.location.assign(caseUpdatePath(caseId, queueHref, "error", result.message));
      else setError(result.message);
    } catch {
      requireReload("The outcome could not be confirmed.");
    }
  }
  return <form action={submit} className="panel stack" aria-labelledby="assignment-heading">
    <h2 id="assignment-heading">Change case assignment</h2>
    <p>Use an eligible person’s ID and explain the change. The analyst and reviewer must be different people. Additional authentication may be required.</p>
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="record_version" value={recordVersion} />
    {error && <p role="alert" className="warn">{error}{uncertain && <> Check the recorded assignment before trying again. <a href={`/cases/${caseId}`}>Reload case</a></>}</p>}
    {error?.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(`/cases/${caseId}`)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to retry.</p>}
    {error?.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to retry.</p>}
    {!uncertain && <>
      <label htmlFor="assignment-role">Assignment role</label>
      <select id="assignment-role" name="role" value={role} onChange={(event) => { searchGeneration.current++; setSearching(false); setRole(event.target.value); setError(null); setPeople([]); setPersonId(""); setSearchMessage(""); }}><option value="ANALYST">Analyst</option><option value="REVIEWER">Reviewer</option></select>
      <p>Currently assigned: {(role === "ANALYST" ? analystId : reviewerId) ?? "No one assigned"}</p>
      <label htmlFor="assignment-search">Search by name</label>
      <input id="assignment-search" type="search" value={query} maxLength={100} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); if (!searching) void searchPeople(); } }} onChange={(event) => { searchGeneration.current++; setSearching(false); setQuery(event.target.value); setPersonId(""); setPeople([]); setSearchMessage(""); }} />
      <button type="button" onClick={searchPeople} disabled={searching} aria-busy={searching}>{searching ? "Searching…" : "Search eligible people"}</button>
      {searchMessage && <p role="status">{searchMessage}</p>}
      {searchMessage.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(`/cases/${caseId}`)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to search again.</p>}
      {searchMessage.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to search again.</p>}
      {people.length > 0 && <ul>{people.map((person) => <li key={person.id}><button type="button" onClick={() => { setPersonId(person.id); setPeople([]); setSearchMessage(`Selected ${person.display_name}.`); }}>{person.display_name} ({person.id})</button></li>)}</ul>}
      <label htmlFor="assignment-person">Person ID</label>
      <input id="assignment-person" name="principal_id" value={personId} onChange={(event) => { setPersonId(event.target.value); setSearchMessage(""); }} required pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}" maxLength={36} aria-describedby="assignment-person-help" />
      <p id="assignment-person-help">Enter the ID of an active, eligible {role === "ANALYST" ? "analyst" : "reviewer"} in your organization. Ask your administrator for their ID if needed.</p>
      <label htmlFor="assignment-reason">Reason for reassignment</label>
      <input id="assignment-reason" name="reason" required minLength={3} maxLength={300} />
      <SubmitButton>Update assignment</SubmitButton>
    </>}
  </form>;
}
