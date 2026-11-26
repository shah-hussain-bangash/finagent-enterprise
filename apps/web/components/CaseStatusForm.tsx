"use client";

import { useState } from "react";
import { updateCase } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function CaseStatusForm({caseId, recordVersion, blocked}: {caseId: string; recordVersion: number; blocked: boolean}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`/cases/${caseId}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await updateCase(form);
      if (result.ok) window.location.assign(`/cases/${caseId}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.message);
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="inline panel">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="record_version" value={recordVersion} />
    <input type="hidden" name="status" value={blocked ? "INVESTIGATING" : "BLOCKED"} />
    {!blocked && <input type="hidden" name="blocked_owner_role" value="ANALYST" />}
    {error && <p role="alert">{error} {uncertain && <>Check the recorded case before another action. <a href={`/cases/${caseId}`}>Reload case</a></>}</p>}
    {!uncertain && <>
      <label htmlFor="creason">{blocked ? "Resolve block" : "Block case"}: reason</label>
      <input id="creason" name="reason" required minLength={3} maxLength={300} />
      <SubmitButton>{blocked ? "Resume investigation" : "Mark blocked"}</SubmitButton>
    </>}
  </form>;
}
