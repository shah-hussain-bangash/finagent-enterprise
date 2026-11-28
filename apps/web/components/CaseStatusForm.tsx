"use client";

import { useState } from "react";
import { updateCase } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { caseUpdatePath } from "@/lib/case-navigation";

export function CaseStatusForm({caseId, recordVersion, blocked, queueHref}: {caseId: string; recordVersion: number; blocked: boolean; queueHref: string}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(caseUpdatePath(caseId, queueHref, "error", warning));
  }
  async function submit(form: FormData) {
    try {
      const result = await updateCase(form);
      if (result.ok) window.location.assign(caseUpdatePath(caseId, queueHref, "ok", result.message));
      else if (result.uncertain) requireReload(result.message);
      else if (result.message.startsWith("VERSION_CONFLICT:")) window.location.assign(caseUpdatePath(caseId, queueHref, "error", result.message));
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
    {error?.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(`/cases/${caseId}`)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to retry.</p>}
    {error?.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to retry.</p>}
    {!uncertain && <>
      <label htmlFor="creason">{blocked ? "Resolve block" : "Block case"}: reason</label>
      <input id="creason" name="reason" required minLength={3} maxLength={300} />
      <SubmitButton>{blocked ? "Resume investigation" : "Mark blocked"}</SubmitButton>
    </>}
  </form>;
}
