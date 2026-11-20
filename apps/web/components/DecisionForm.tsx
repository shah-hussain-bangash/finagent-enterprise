"use client";

import { useState, type ReactNode } from "react";

export function DecisionForm({action, refreshHref, children}: {
  action: (form: FormData) => Promise<{ok: boolean; message: string}>;
  refreshHref: string;
  children: ReactNode;
}) {
  const [result, setResult] = useState<{ok: boolean; message: string} | null>(null);
  async function submit(form: FormData) {
    try { setResult(await action(form)); }
    catch { setResult({ok: false, message: "Could not confirm the decision. Reload the review before trying again."}); }
  }
  return <form action={submit} className="stack panel">
    {!result && children}
    {result && <p role={result.ok ? "status" : "alert"}>{result.message} <a href={refreshHref}>Reload review</a></p>}
  </form>;
}
