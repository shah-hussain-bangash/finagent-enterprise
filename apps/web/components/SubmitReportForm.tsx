"use client";

import { useState } from "react";
import { submitReport } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function SubmitReportForm({caseId, version, requestKey}: {caseId: string; version: number; requestKey: string}) {
  const [result, setResult] = useState<{ok: boolean; message: string} | null>(null);
  const [uncertain, setUncertain] = useState(false);
  async function submit(form: FormData) {
    try { const response = await submitReport(form); setUncertain(response.uncertain === true); setResult(response); }
    catch { setUncertain(true); setResult({ok: false, message: "Could not confirm the request. Reload the report before trying again."}); }
  }
  return <form action={submit} className="inline panel">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="version_no" value={version} />
    <input type="hidden" name="key" value={requestKey} />
    {!result?.ok && !uncertain && <SubmitButton>{`Submit version ${version} for independent review`}</SubmitButton>}
    {result && <p role={result.ok ? "status" : "alert"}>{result.message} {(result.ok || uncertain) && <a href={`/cases/${caseId}/report`}>Refresh report</a>}</p>}
  </form>;
}
