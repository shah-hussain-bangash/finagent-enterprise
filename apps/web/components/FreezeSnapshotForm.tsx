"use client";

import { useState } from "react";
import { freezeSnapshot } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function FreezeSnapshotForm({caseId}: {caseId: string}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const path = `/cases/${caseId}/evidence`;
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`${path}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await freezeSnapshot(form);
      if (result.ok) window.location.assign(`${path}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.message);
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="inline">
    <input type="hidden" name="case_id" value={caseId} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded evidence before another action. <a href={path}>Reload evidence</a></>}</p>}
    {!uncertain && <SubmitButton>Freeze current evidence</SubmitButton>}
  </form>;
}
