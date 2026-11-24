"use client";

import { useState } from "react";
import { resumeRun } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function ResumeWorkflowForm({runId, action, returnPath, label, button}: {
  runId: string; action: string; returnPath: string; label: string; button: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  function requireReload(path: string, message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`${path}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await resumeRun(form);
      if (result.ok) window.location.assign(`${result.back}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.back, result.message);
      else setError(result.message);
    } catch {
      requireReload(returnPath, "Could not confirm the request.");
    }
  }
  return <form action={submit} className="stack panel">
    <input type="hidden" name="run_id" value={runId} />
    <input type="hidden" name="action" value={action} />
    <input type="hidden" name="back" value={returnPath} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded status before another action. <a href={returnPath}>Reload status</a></>}</p>}
    {!uncertain && <>
      <label htmlFor={`resume-${action}`}>{label}</label>
      <input id={`resume-${action}`} name="reason" required minLength={5} maxLength={300} />
      <SubmitButton>{button}</SubmitButton>
    </>}
  </form>;
}
