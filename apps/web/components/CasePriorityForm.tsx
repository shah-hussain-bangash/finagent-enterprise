"use client";

import { useState } from "react";
import { updateCase } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";
import { caseUpdatePath } from "@/lib/case-navigation";

export function CasePriorityForm({ caseId, recordVersion, priority, queueHref }: { caseId: string; recordVersion: number; priority: string; queueHref: string }) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  function requireReload(message: string) {
    setError(message);
    setUncertain(true);
    const warning = "CONNECTION_ERROR: The priority change may have been recorded; check the case before trying again.";
    window.location.assign(caseUpdatePath(caseId, queueHref, "error", warning));
  }
  async function submit(form: FormData) {
    if (String(form.get("priority") ?? "") === priority) {
      setError("This case already has that priority. Choose a different priority to make a change.");
      return;
    }
    try {
      const result = await updateCase(form);
      if (result.ok) window.location.assign(caseUpdatePath(caseId, queueHref, "ok", result.message));
      else if (result.uncertain) requireReload(result.message);
      else if (result.message.startsWith("VERSION_CONFLICT:")) window.location.assign(caseUpdatePath(caseId, queueHref, "error", result.message));
      else setError(result.message);
    } catch {
      requireReload("The priority change could not be confirmed.");
    }
  }
  return <form action={submit} className="panel stack" aria-labelledby="priority-heading">
    <h2 id="priority-heading">Change case priority</h2>
    <p>Record the reason for changing this case’s priority. A change made by someone else requires a reload before another update.</p>
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="record_version" value={recordVersion} />
    {error && <p role="alert" className="warn">{error}{uncertain && <> Check the recorded case before trying again. <a href={`/cases/${caseId}`}>Reload case</a></>}</p>}
    {error?.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(`/cases/${caseId}`)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to retry.</p>}
    {error?.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to retry.</p>}
    {!uncertain && <>
      <label htmlFor="case-priority">New priority</label>
      <select id="case-priority" name="priority" defaultValue={["LOW", "NORMAL", "HIGH", "URGENT"].includes(priority) ? priority : ""} onChange={() => setError(null)} required>
        <option value="" disabled>Choose a priority</option>
        <option value="LOW">Low</option><option value="NORMAL">Normal</option><option value="HIGH">High</option><option value="URGENT">Urgent</option>
      </select>
      <label htmlFor="priority-reason">Reason for the change</label>
      <input id="priority-reason" name="reason" required minLength={3} maxLength={300} />
      <SubmitButton>Update priority</SubmitButton>
    </>}
  </form>;
}
