"use client";

import { useState } from "react";
import { editReport } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

type Section = {section_id: string; title: string; text: string; claim_ids: string[]};

export function ReportEditForm({caseId, version, sections}: {caseId: string; version: number; sections: Section[]}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [texts, setTexts] = useState<Record<string, string>>(() => Object.fromEntries(sections.map((s) => [s.section_id, s.text])));
  const [note, setNote] = useState("");
  const path = `/cases/${caseId}/report`;
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`${path}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await editReport(form);
      if (result.ok) window.location.assign(`${path}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.message);
      else if (result.sessionExpired) {
        setSessionExpired(true);
        setError("Your session expired. Copy your edits before leaving this page. Sign in in a new tab, then reload the report and check it before saving again.");
      }
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="stack">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="base_version_no" value={version} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded report before another action. <a href={path}>Reload report</a></>}</p>}
    {!uncertain && <>
      {sections.map((s) => <div key={s.section_id} className="panel">
        <input type="hidden" name="section_id" value={s.section_id} />
        <label htmlFor={`s-${s.section_id}`}>{s.title}</label>
        <textarea id={`s-${s.section_id}`} name="section_text" value={texts[s.section_id] ?? ""} onChange={(event) => setTexts({ ...texts, [s.section_id]: event.target.value })} />
        {s.claim_ids.length ? <p className="muted">Backed by claims: {s.claim_ids.join(", ")}</p> : null}
      </div>)}
      <label htmlFor="edit_note">Edit note (required)</label>
      <input id="edit_note" name="edit_note" required minLength={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
      {sessionExpired ? <a href={`/signin?expired=1&returnTo=${encodeURIComponent(path)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a> : <SubmitButton>Save as new version</SubmitButton>}
    </>}
  </form>;
}
