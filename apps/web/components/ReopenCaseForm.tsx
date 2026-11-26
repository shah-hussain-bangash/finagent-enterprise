"use client";

import { useState } from "react";
import { reopenCase } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function ReopenCaseForm({caseId, recordVersion}: {caseId: string; recordVersion: number}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  async function submit(form: FormData) {
    try {
      const result = await reopenCase(form);
      if (result.ok) window.location.assign(`/cases/${caseId}?ok=${encodeURIComponent(result.message)}`);
      else { setUncertain(result.uncertain === true); setError(result.message); }
    } catch { setUncertain(true); setError("Could not confirm the request. Reload the case before trying again."); }
  }
  return <form action={submit} className="stack" aria-label="Reopen completed case">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="record_version" value={recordVersion} />
    {error && <p role="alert">{error} {uncertain && <a href={`/cases/${caseId}`}>Reload case</a>}</p>}
    {!uncertain && <>
    <label htmlFor="reopen-reason">Reason for reopening</label>
    <textarea id="reopen-reason" name="reason" required minLength={3} maxLength={300} rows={3} aria-describedby="reopen-help" />
    <SubmitButton>Reopen case</SubmitButton>
    </>}
  </form>;
}
