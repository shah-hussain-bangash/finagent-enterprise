"use client";

import { useState } from "react";
import { uploadDocument } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function UploadDocumentForm({caseId, requestKey}: {caseId: string; requestKey: string}) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const path = `/cases/${caseId}/documents`;
  function requireReload(message: string) {
    setUncertain(true);
    setError(message);
    const warning = "CONNECTION_ERROR: The outcome may be unknown; check the record before trying again.";
    window.location.assign(`${path}?error=${encodeURIComponent(warning)}`);
  }
  async function submit(form: FormData) {
    try {
      const result = await uploadDocument(form);
      if (result.ok) window.location.assign(`${path}?ok=${encodeURIComponent(result.message)}`);
      else if (result.uncertain) requireReload(result.message);
      else setError(result.message);
    } catch {
      requireReload("Could not confirm the request.");
    }
  }
  return <form action={submit} className="stack panel">
    <input type="hidden" name="case_id" value={caseId} />
    <input type="hidden" name="key" value={requestKey} />
    {error && <p role="alert">{error} {uncertain && <>Check the recorded documents before another action. <a href={path}>Reload documents</a></>}</p>}
    {error?.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(path)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to retry.</p>}
    {error?.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to retry.</p>}
    {!uncertain && <>
      <label htmlFor="file">File (PDF, DOCX or CSV; up to 20 MiB; scanned before use)</label>
      <input id="file" name="file" type="file" accept=".pdf,.docx,.csv" required />
      <label htmlFor="document_type">Document type</label>
      <select id="document_type" name="document_type">
        <option value="PASSPORT">Passport</option>
        <option value="PROOF_OF_ADDRESS">Proof of address</option>
        <option value="BANK_STATEMENT">Bank statement</option>
        <option value="SOURCE_OF_FUNDS">Source of funds</option>
        <option value="OTHER">Other</option>
      </select>
      <SubmitButton>Upload</SubmitButton>
    </>}
  </form>;
}
