"use client";

import { useState } from "react";
import { resolveConflict } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function ResolveConflictForm({caseId, conflictId}: {caseId: string; conflictId: string}) {
  const [result, setResult] = useState<{ok: boolean; message: string} | null>(null);
  const [uncertain, setUncertain] = useState(false);
  async function submit(form: FormData) {
    try { const response = await resolveConflict(form); setUncertain(response.uncertain === true); setResult(response); }
    catch { setUncertain(true); setResult({ok: false, message: "Could not confirm the request. Reload the evidence before trying again."}); }
  }
  return <form action={submit} className="stack">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="conflict_id" value={conflictId} />
    {!result?.ok && !uncertain && <>
      <label htmlFor={`r-${conflictId}`}>Resolution</label>
      <select id={`r-${conflictId}`} name="resolution" defaultValue="" required>
        <option value="" disabled>Choose a resolution</option>
        <option value="USE_A">Use source A</option>
        <option value="USE_B">Use source B</option>
        <option value="BOTH_RETAINED_EXPLAINED">Keep both, explained</option>
        <option value="SOURCE_CORRECTION_REQUESTED">Request a source correction</option>
      </select>
      <label htmlFor={`t-${conflictId}`}>Reason (recorded; neither source is deleted)</label>
      <textarea id={`t-${conflictId}`} name="rationale" required minLength={10} maxLength={1000} />
      <SubmitButton>Record resolution</SubmitButton>
    </>}
    {result && <p role={result.ok ? "status" : "alert"}>{result.message} {(result.ok || uncertain) && <a href={`/cases/${caseId}/evidence`}>Refresh evidence</a>}</p>}
  </form>;
}
