"use client";

import { useState } from "react";
import { uploadDocumentVersion } from "@/app/actions";
import { SubmitButton } from "@/components/SubmitButton";

export function UploadDocumentVersionForm({ documentId, requestKey, returnTo }: { documentId: string; requestKey: string; returnTo?: string }) {
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const params = new URLSearchParams(returnTo ? {returnTo} : {});
  const path = `/documents/${documentId}${params.size ? `?${params}` : ""}`;
  const messagePath = (kind: "ok" | "error", message: string) => {
    const query = new URLSearchParams(returnTo ? {returnTo} : {});
    query.set(kind, message);
    return `/documents/${documentId}?${query}`;
  };
  function checkRecordedVersion() {
    setUncertain(true);
    const warning = "CONNECTION_ERROR: The upload may have been recorded. Check the version history before another upload.";
    window.location.assign(messagePath("error", warning));
  }
  async function submit(form: FormData) {
    try {
      const result = await uploadDocumentVersion(form);
      if (result.ok) window.location.assign(messagePath("ok", result.message));
      else if (result.uncertain) checkRecordedVersion();
      else setError(result.message);
    } catch {
      checkRecordedVersion();
    }
  }
  return <form action={submit} className="panel stack" aria-labelledby="upload-version-heading">
    <h2 id="upload-version-heading">Upload a new version</h2>
    <p>Use a corrected PDF, DOCX or CSV up to 20 MiB. Previous versions remain in the history; the new file must be scanned and processed.</p>
    <input type="hidden" name="document_id" value={documentId} />
    <input type="hidden" name="key" value={requestKey} />
    {error && <p role="alert">{error}</p>}
    {error?.startsWith("SESSION_EXPIRED:") && <p><a href={`/signin?expired=1&returnTo=${encodeURIComponent(path)}`} target="_blank" rel="noopener noreferrer">Sign in in a new tab</a>, then return here to retry.</p>}
    {error?.startsWith("MFA_REQUIRED:") && <p><a href="/security" target="_blank" rel="noopener noreferrer">Open account security in a new tab</a>, then return here to retry.</p>}
    {uncertain ? <p role="alert">Check the version history before uploading again. <a href={path}>Reload document</a></p> : <>
      <label htmlFor="version-file">Corrected document file</label>
      <input id="version-file" name="file" type="file" accept=".pdf,.docx,.csv" required />
      <SubmitButton>Upload new version</SubmitButton>
    </>}
  </form>;
}
