"use client";

import { useState } from "react";
import { correctField } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function CorrectDocumentFieldForm({documentId, fieldId, fieldKey, fieldsPage = 1, returnTo}: {documentId: string; fieldId: string; fieldKey: string; fieldsPage?: number; returnTo?: string}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const baseParams = new URLSearchParams({ ...(fieldsPage > 1 ? {fields_page: String(fieldsPage)} : {}), ...(returnTo ? {returnTo} : {}) });
  const path = `/documents/${documentId}${baseParams.size ? `?${baseParams}` : ""}`;
  function pathWithMessage(kind: "ok" | "error", message: string) {
    const params = new URLSearchParams();
    if (fieldsPage > 1) params.set("fields_page", String(fieldsPage));
    if (returnTo) params.set("returnTo", returnTo);
    params.set(kind, message);
    return `/documents/${documentId}?${params}`;
  }
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(pathWithMessage("error", warning));
  }
  async function submit(form: FormData) {
    try {
      const result = await correctField(form);
      if (result.ok) window.location.assign(pathWithMessage("ok", result.message));
      else if (result.uncertain) requireReload(result.message);
      else if (result.message.startsWith("VERSION_CONFLICT:")) window.location.assign(pathWithMessage("error", result.message));
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="stack">
    <input type="hidden" name="document_id" value={documentId} />
    <input type="hidden" name="field_id" value={fieldId} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded document before another action. <a href={path}>Reload document</a></>}</p>}
    {error?.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(path)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to retry.</p>}
    {error?.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to retry.</p>}
    {!uncertain && <>
      <label htmlFor={`v-${fieldId}`}>New value for {fieldKey}</label>
      <input id={`v-${fieldId}`} name="value" required maxLength={1000} />
      <label htmlFor={`r-${fieldId}`}>Reason for correcting {fieldKey}</label>
      <input id={`r-${fieldId}`} name="reason" required minLength={10} maxLength={300} />
      <SubmitButton>{`Save correction for ${fieldKey}`}</SubmitButton>
    </>}
  </form>;
}
