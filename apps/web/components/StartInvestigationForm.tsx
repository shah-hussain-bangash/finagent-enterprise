"use client";

import { useState } from "react";
import { startInvestigation } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function StartInvestigationForm({ caseId, requestKey }: { caseId: string; requestKey: string }) {
  const [result, setResult] = useState<{ok: boolean; message: string} | null>(null);
  const [uncertain, setUncertain] = useState(false);
  async function submit(form: FormData) {
    try {
      const response = await startInvestigation(form);
      setUncertain(response.uncertain === true);
      setResult(response);
    } catch {
      setUncertain(true);
      setResult({ok: false, message: "Could not confirm the request. Reload the case before trying again."});
    }
  }
  return <form action={submit} className="inline panel">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="key" value={requestKey} />
    {!result?.ok && !uncertain && <SubmitButton>Start investigation</SubmitButton>}
    {result && <p role={result.ok ? "status" : "alert"}>{result.message} {(result.ok || uncertain) && <a href={`/cases/${caseId}`}>Refresh case</a>}</p>}
    <span className="muted">Gathers evidence through approved connectors and drafts a report for your review.</span>
  </form>;
}
