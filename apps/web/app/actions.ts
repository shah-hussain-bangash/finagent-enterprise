"use server";
// Server actions: every mutation goes to the API with the session's bearer token. Next.js checks the
// Origin of server-action requests (CSRF). The UI shows the REAL result: success only after the API
// confirmed it, otherwise the API's error code/message (e.g. 409 stale version) - never optimistic.
import { randomUUID } from "node:crypto";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { api, ApiRequestError, raw } from "@/lib/api";
import { clearSession } from "@/lib/session";
import { authMode } from "@/lib/auth-config";
import { startDevelopmentSession } from "@/lib/development-session";

function str(f: FormData, k: string): string {
  const v = f.get(k);
  return typeof v === "string" ? v.trim() : "";
}

function to(path: string, kind: "ok" | "error", message: string): never {
  const sep = path.includes("?") ? "&" : "?";
  redirect(`${path}${sep}${kind}=${encodeURIComponent(message.slice(0, 300))}`);
}

async function run(back: string, okMessage: string, fn: () => Promise<unknown>): Promise<never> {
  let failure: string | null = null;
  try {
    await fn();
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    const problems = (e.body?.error.details as { problems?: { code: string }[] } | undefined)?.problems;
    failure = `${e.code}: ${e.message}${problems ? " (" + problems.map((p) => p.code).join(", ") + ")" : ""}`;
  }
  return failure ? to(back, "error", failure) : to(back, "ok", okMessage);
}

// ------------------------------------------------------------------ session (LOCAL/TEST issuer only)
export async function signIn(form: FormData): Promise<never> {
  if (authMode() !== "local") redirect("/signin?error=Development%20sign-in%20is%20disabled.");
  const subject = str(form, "subject");
  if (!await startDevelopmentSession(subject, str(form, "mfa") !== "no")) {
    to("/signin", "error", "Sign-in failed or the development issuer is disabled.");
  }
  redirect("/");
}

export async function signOut(): Promise<never> {
  await clearSession();
  redirect("/signin");
}

// ------------------------------------------------------------------ cases and workflows
export async function startInvestigation(form: FormData): Promise<{ok: boolean; message: string}> {
  const caseId = str(form, "case_id");
  try {
    await api("/api/v1/workflows", { json: { case_id: caseId }, idempotencyKey: str(form, "key") || randomUUID() });
    return {ok: true, message: "Investigation queued."};
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    return {ok: false, message: `${e.code}: ${e.message}`};
  }
}

export async function resumeRun(form: FormData): Promise<never> {
  const runId = str(form, "run_id");
  return run(str(form, "back") || `/workflows/${runId}`, "Resume requested.", () =>
    api(`/api/v1/workflows/${runId}/resume`, { json: { action: str(form, "action"), reason: str(form, "reason") } }));
}

export async function cancelRun(form: FormData): Promise<never> {
  const runId = str(form, "run_id");
  return run(`/workflows/${runId}`, "Cancellation requested.", () =>
    api(`/api/v1/workflows/${runId}/cancel`, { json: { reason: str(form, "reason") } }));
}

export async function updateCase(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  const body: Record<string, string> = { reason: str(form, "reason") };
  for (const k of ["status", "priority", "blocked_owner_role"]) if (str(form, k)) body[k] = str(form, k);
  return run(`/cases/${caseId}`, "Case updated.", () =>
    api(`/api/v1/cases/${caseId}`, { method: "PATCH", json: body, ifMatch: Number(str(form, "record_version")) }));
}

export async function reopenCase(form: FormData): Promise<{ok: boolean; message: string}> {
  const caseId = str(form, "case_id");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(caseId)) {
    return {ok: false, message: "Choose a valid case."};
  }
  const back = `/cases/${caseId}`;
  const reason = str(form, "reason");
  const versionText = str(form, "record_version");
  const version = Number(versionText);
  if (!/^[1-9]\d*$/.test(versionText) || !Number.isSafeInteger(version)) {
    return {ok: false, message: "Reload this case before reopening it."};
  }
  if (reason.length < 3 || reason.length > 300) {
    return {ok: false, message: "Enter a reason between 3 and 300 characters."};
  }
  try {
    // Use the version the supervisor actually viewed. Never fetch a newer version
    // here to turn an outdated form into an accepted mutation.
    await api(`/api/v1/cases/${caseId}/reopen`, {
      method: "POST", json: { reason }, ifMatch: version,
    });
    revalidatePath(back);
    revalidatePath(`${back}/report`);
    revalidatePath(`${back}/audit`);
    revalidatePath("/cases");
    return {ok: true, message: "Case reopened. A new draft requires review."};
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    return {ok: false, message: `${e.code}: ${e.message}`};
  }
}

export async function reassign(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  return run(`/cases/${caseId}`, "Assignment changed.", () =>
    api(`/api/v1/cases/${caseId}/assignment`, {
      json: { principal_id: str(form, "principal_id"), role: str(form, "role"), reason: str(form, "reason") },
    }));
}

// ------------------------------------------------------------------ evidence and documents
export async function resolveConflict(form: FormData): Promise<{ok: boolean; message: string}> {
  const caseId = str(form, "case_id");
  try {
    await api(`/api/v1/cases/${caseId}/evidence-conflicts/${str(form, "conflict_id")}/resolution`, {
      json: { resolution: str(form, "resolution"), rationale: str(form, "rationale") },
    });
    return {ok: true, message: "Conflict resolved; both source values are kept."};
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    return {ok: false, message: `${e.code}: ${e.message}`};
  }
}

export async function freezeSnapshot(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  return run(`/cases/${caseId}/evidence`, "Snapshot request processed.", () =>
    api(`/api/v1/cases/${caseId}/snapshots`, { method: "POST" }));
}

export async function uploadDocument(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) to(`/cases/${caseId}/documents`, "error", "Choose a file to upload.");
  const f = file as File;
  const q = new URLSearchParams({ case_id: caseId, document_type: str(form, "document_type"), filename: f.name });
  return run(`/cases/${caseId}/documents`, "Uploaded. Scanning and parsing status is shown below.", async () => {
    const res = await raw(`/api/v1/documents?${q}`, {
      method: "POST",
      body: Buffer.from(await f.arrayBuffer()),
      headers: { "Content-Type": f.type || "application/octet-stream" },
      idempotencyKey: str(form, "key") || randomUUID(),
    });
    if (!res.ok) throw new ApiRequestError(res.status, await res.json().catch(() => null));
  });
}

export async function correctField(form: FormData): Promise<never> {
  const docId = str(form, "document_id");
  return run(`/documents/${docId}`, "Correction saved; the original value is preserved.", () =>
    api(`/api/v1/documents/${docId}/fields/${str(form, "field_id")}/correction`, {
      json: { value: str(form, "value"), reason: str(form, "reason") },
    }));
}

// ------------------------------------------------------------------ reports and review
export async function editReport(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  const sections = form.getAll("section_id").map((id, i) => ({
    section_id: String(id),
    text: String(form.getAll("section_text")[i] ?? ""),
  })).filter((s) => s.text.trim().length > 0);
  return run(`/cases/${caseId}/report`, "Saved as a new report version.", () =>
    api(`/api/v1/cases/${caseId}/reports/edits`, {
      json: { base_version_no: Number(str(form, "base_version_no")), edit_note: str(form, "edit_note"), sections },
    }));
}

export async function submitReport(form: FormData): Promise<{ok: boolean; message: string}> {
  const caseId = str(form, "case_id");
  try {
    await api(`/api/v1/cases/${caseId}/reports/submit`, {
      json: { version_no: Number(str(form, "version_no")) },
      idempotencyKey: str(form, "key") || randomUUID(),
    });
    return {ok: true, message: "Submitted for independent review."};
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    return {ok: false, message: `${e.code}: ${e.message}`};
  }
}

export async function withdrawReport(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  return run(`/cases/${caseId}/report`, "Submission withdrawn.", () =>
    api(`/api/v1/cases/${caseId}/reports/withdraw`, { json: { reason: str(form, "reason") } }));
}

export async function decide(form: FormData): Promise<{ok: boolean; message: string}> {
  const id = str(form, "approval_request_id");
  try {
    const res = await raw(`/api/v1/approvals/${id}/decision`, {
      json: {
        decision: str(form, "decision"),
        rationale: str(form, "rationale"),
        report_version_id: str(form, "report_version_id"),
        snapshot_id: str(form, "snapshot_id"),
      },
      idempotencyKey: str(form, "key") || randomUUID(),
    });
    if (!res.ok) throw new ApiRequestError(res.status, await res.json().catch(() => null));
    return {ok: true, message: "Decision recorded."};
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    return {ok: false, message: `${e.code}: ${e.message}`};
  }
}

export async function createExport(form: FormData): Promise<never> {
  const caseId = str(form, "case_id");
  let exportId: string | null = null;
  let failure = "Export failed.";
  try {
    const out = await api<{ export_id: string }>("/api/v1/exports", {
      json: { case_id: caseId, ...(str(form, "version_no") ? { version_no: Number(str(form, "version_no")) } : {}) },
    });
    exportId = out.export_id;
  } catch (e) {
    if (!(e instanceof ApiRequestError)) throw e;
    failure = `${e.code}: ${e.message}`;
  }
  if (!exportId) to(`/cases/${caseId}/report`, "error", failure);
  redirect(`/exports/${exportId}`); // route handler streams the authorized file
}

// ------------------------------------------------------------------ knowledge
export async function policyLifecycle(form: FormData): Promise<never> {
  return run("/policies/admin", "Policy version updated.", () =>
    api(`/api/v1/knowledge/policy-versions/${str(form, "version_id")}/${str(form, "action")}`, {
      json: str(form, "action") === "reindex" ? undefined : { reason: str(form, "reason") },
      method: "POST",
    }));
}

export async function syncPolicies(): Promise<never> {
  return run("/policies/admin", "Policy repository synchronised (new versions arrive as DRAFT).", () =>
    api("/api/v1/knowledge/sync", { method: "POST" }));
}

// ------------------------------------------------------------------ operations and administration
export async function reconcile(form: FormData): Promise<never> {
  return run("/operations", "Reconciliation recorded; the run is handed back for review.", () =>
    api(`/api/v1/operations/${str(form, "operation_id")}/reconcile`, {
      json: { resolution: str(form, "resolution"), reason: str(form, "reason") },
    }));
}

export async function setFlag(form: FormData): Promise<never> {
  return run("/admin", "Feature flag updated.", () =>
    api(`/api/v1/admin/feature-flags/${str(form, "flag_key")}`, {
      method: "PUT",
      json: { enabled: str(form, "enabled") === "true", reason: str(form, "reason") },
    }));
}

export async function createDelegation(form: FormData): Promise<never> {
  const days = Number(str(form, "days") || "1");
  return run("/admin", "Delegation created.", () =>
    api("/api/v1/admin/delegations", {
      json: {
        delegator_id: str(form, "delegator_id"),
        delegate_id: str(form, "delegate_id"),
        role: str(form, "role"),
        reason: str(form, "reason"),
        valid_to: new Date(Date.now() + days * 86400_000).toISOString(),
      },
    }));
}

export async function revokeDelegation(form: FormData): Promise<never> {
  return run("/admin", "Delegation revoked.", () =>
    api(`/api/v1/admin/delegations/${str(form, "delegation_id")}/revoke`, { method: "POST" }));
}

export async function startEvaluation(form: FormData): Promise<never> {
  return run("/evaluations", "Evaluation run completed.", () =>
    api("/api/v1/evaluations", { json: { dataset: str(form, "dataset") } }));
}


// Codes stay in the same-origin server action and API; never in a URL or session record.
export async function verifyAuthenticator(form: FormData): Promise<never> {
  const code = form.get("code");
  if (typeof code !== "string" || !/^[0-9]{6}$/.test(code)
      || form.getAll("code").length !== 1) {
    redirect("/security?error=invalid-code");
  }
  return run("/security", "Authenticator verified.", () =>
    api("/api/v1/auth/totp/verify", { json: { code } }));
}


export async function sendEmailVerification(): Promise<never> {
  return run("/security/email", "Verification email requested.", () =>
    api("/api/v1/auth/email/send", { method: "POST" }));
}

export async function confirmEmailVerification(form: FormData): Promise<never> {
  const code=form.get("code"), challenge=form.get("challenge_id");
  if (typeof code!=="string" || !/^[0-9]{6}$/.test(code) || form.getAll("code").length!==1
      || typeof challenge!=="string" || !/^[0-9a-f-]{36}$/.test(challenge)
      || form.getAll("challenge_id").length!==1) redirect("/security/email?error=invalid-code");
  return run("/security/email", "Email verified.", () =>
    api("/api/v1/auth/email/verify", { json: { code, challenge_id: challenge } }));
}
