"use client";

import { useState } from "react";
import { correctField } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function CorrectDocumentFieldForm({documentId, fieldId, fieldKey}: {documentId: string; fieldId: string; fieldKey: string}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const path = `/documents/${documentId}`;
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`${path}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await correctField(form);
      if (result.ok) window.location.assign(`${path}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.message);
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="stack">
    <input type="hidden" name="document_id" value={documentId} />
    <input type="hidden" name="field_id" value={fieldId} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded document before another action. <a href={path}>Reload document</a></>}</p>}
    {!uncertain && <>
      <label htmlFor={`v-${fieldId}`}>New value for {fieldKey}</label>
      <input id={`v-${fieldId}`} name="value" required maxLength={1000} />
      <label htmlFor={`r-${fieldId}`}>Reason for correcting {fieldKey}</label>
      <input id={`r-${fieldId}`} name="reason" required minLength={10} maxLength={300} />
      <SubmitButton>{`Save correction for ${fieldKey}`}</SubmitButton>
    </>}
  </form>;
}
