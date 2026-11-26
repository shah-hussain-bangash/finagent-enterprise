"use client";

import { useState } from "react";
import { cancelRun, withdrawReport } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function RecoveryReasonForm({kind, recordId}: {kind: "cancel" | "withdraw"; recordId: string}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const cancel = kind === "cancel";
  const path = cancel ? `/workflows/${recordId}` : `/cases/${recordId}/report`;
  const label = cancel ? "Cancel (committed effects are kept): reason" : "Reason for withdrawing the current submission";
  const button = cancel ? "Cancel run" : "Withdraw current submission";
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`${path}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await (cancel ? cancelRun(form) : withdrawReport(form));
      if (result.ok) window.location.assign(`${path}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.message);
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="inline panel">
    <input type="hidden" name={cancel ? "run_id" : "case_id"} value={recordId} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded status before another action. <a href={path}>Reload status</a></>}</p>}
    {!uncertain && <>
      <label htmlFor={`reason-${kind}`}>{label}</label>
      <input id={`reason-${kind}`} name="reason" required minLength={cancel ? 3 : 5} maxLength={300} />
      <SubmitButton danger>{button}</SubmitButton>
    </>}
  </form>;
}
