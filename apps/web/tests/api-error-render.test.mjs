import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function compile(relativePath, mocks) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports;
}
const { ApiRequestError, api, raw } = compile("../lib/api.ts", {
  "server-only": {}, "next/navigation": { redirect: (path) => { throw new Error(`redirect:${path}`); } },
  "@/lib/session": { requireSession: async () => ({ token: "synthetic" }) },
});
const { ErrorState, Flash, Tabs } = compile("../components/ui.tsx", {
  "next/link": { __esModule: true, default: ({ href, children, ...props }) => React.createElement("a", { href, ...props }, children) },
});

test("case navigation preserves the selected case when opening transactions", () => {
  const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const html = renderToStaticMarkup(React.createElement(Tabs, { caseId, active: "/evidence" }));
  assert.ok(html.includes(`href="/workspace/recorded-transactions?case=${caseId}"`));
  assert.ok(html.includes(`href="/workspace/case-overview?case=${caseId}"`));
  assert.match(html, /href="\/cases\/[^\"]+\/evidence" aria-current="page"/);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
});

test("an interrupted write is not described as definitely unsaved", () => {
  const message = "The connection was interrupted. The outcome may be unknown; check the record before trying again.";
  const html = renderToStaticMarkup(React.createElement(Flash, { error: message }));
  assert.match(html, /Action not confirmed/);
  assert.match(html, /outcome may be unknown/);
  assert.doesNotMatch(html, /Not saved/);
});

for (const body of [null, {}, { error: null }, { error: { message: {}, code: {}, correlation_id: {} } }]) {
  test(`renders a safe fallback for malformed API errors: ${JSON.stringify(body)}`, () => {
    const error = new ApiRequestError(502, body);
    assert.equal(error.message, "Request failed (502)");
    assert.equal(error.code, "HTTP_502");
    const html = renderToStaticMarkup(React.createElement(ErrorState, { error, what: "Cases" }));
    assert.match(html, /Could not load cases/);
    assert.match(html, /Request failed \(502\)/);
    assert.doesNotMatch(html, /Correlation ID/);
  });
}

test("preserves a valid access-denied message and correlation ID", () => {
  const error = new ApiRequestError(403, { error: { code: "FORBIDDEN", message: "Access denied", correlation_id: "trace-123" } });
  const html = renderToStaticMarkup(React.createElement(ErrorState, { error, what: "Case" }));
  assert.match(html, /Not permitted/);
  assert.match(html, /Access denied/);
  assert.match(html, /trace-123/);
});

test("a malformed unauthorized response still redirects to sign-in", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("{}", { status: 401, headers: { "content-type": "application/json" } });
  try {
    await assert.rejects(() => raw("/synthetic"), /redirect:\/signin\?expired=1/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a failed read shows an unavailable state without exposing network details", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("sensitive internal address"); };
  try {
    await assert.rejects(() => raw("/synthetic"), (error) => {
      assert.equal(error.code, "CONNECTION_ERROR");
      const html = renderToStaticMarkup(React.createElement(ErrorState, { error, what: "Cases" }));
      assert.match(html, /service could not be reached/);
      assert.doesNotMatch(html, /sensitive internal address|status 503/);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a failed write warns that its outcome may be unknown", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("synthetic failure"); };
  try {
    await assert.rejects(() => raw("/synthetic", { json: { value: 1 } }), (error) => {
      assert.equal(error.code, "CONNECTION_ERROR");
      assert.match(error.message, /outcome may be unknown; check the record/);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("an unreadable successful write response requires checking the recorded outcome", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("not-json", { status: 200 });
  try {
    await assert.rejects(() => api("/synthetic", { json: { value: 1 } }), (error) => {
      assert.equal(error.code, "CONNECTION_ERROR");
      assert.match(error.message, /outcome may be unknown; check the record/);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("an unreadable successful read response reports a safe error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("not-json", { status: 200 });
  try {
    await assert.rejects(() => api("/synthetic"), (error) => {
      assert.equal(error.code, "INVALID_RESPONSE");
      assert.match(error.message, /response could not be read/);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a server error after a write warns that the outcome may be unknown", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("gateway error", { status: 502 });
  try {
    await assert.rejects(() => raw("/synthetic", { method: "PATCH" }), (error) => {
      assert.equal(error.code, "CONNECTION_ERROR");
      assert.match(error.message, /outcome may be unknown; check the record/);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a timed-out write reports an uncertain outcome", async () => {
  const originalFetch = globalThis.fetch;
  // AbortSignal.timeout uses an unreferenced timer in Node; keep the test alive until it fires.
  const keepAlive = setTimeout(() => {}, 1000);
  globalThis.fetch = async (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("synthetic timeout")), { once: true });
  });
  try {
    await assert.rejects(() => raw("/synthetic", { method: "PATCH", timeoutMs: 10 }), (error) => {
      assert.equal(error.code, "CONNECTION_ERROR");
      assert.match(error.message, /outcome may be unknown; check the record/);
      return true;
    });
  } finally {
    clearTimeout(keepAlive);
    globalThis.fetch = originalFetch;
  }
});

test("a server error on a read remains available for normal error handling", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("unavailable", { status: 503 });
  try {
    assert.equal((await raw("/synthetic")).status, 503);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("form actions handle malformed error details and preserve valid problem codes", async () => {
  let currentError;
  const { syncPolicies } = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} },
    "next/navigation": { redirect: (path) => { throw new Error(`redirect:${path}`); } },
    "@/lib/api": { ApiRequestError, api: async () => { throw currentError; }, BASE: "", raw: () => {} },
    "@/lib/session": {}, "@/lib/auth-config": {},
  });
  for (const body of [{}, { error: { details: { problems: "invalid" } } }, { error: { details: { problems: [null, {}, { code: 42 }] } } }]) {
    currentError = new ApiRequestError(502, body);
    await assert.rejects(() => syncPolicies(), /redirect:\/policies\/admin\?error=HTTP_502/);
  }
  currentError = new ApiRequestError(409, { error: { code: "CONFLICT", message: "Review needed", details: { problems: [{ code: "STALE" }] } } });
  await assert.rejects(() => syncPolicies(), (error) => error.message.includes("STALE"));
});

test("interrupted mutations return an explicit uncertain outcome", async () => {
  const error = new ApiRequestError(503, { error: { code: "CONNECTION_ERROR", message: "Outcome may be unknown", correlation_id: "", details: {} } });
  const actions = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} },
    "next/navigation": { redirect: () => {} },
    "@/lib/api": { ApiRequestError, api: async () => { throw error; }, raw: async () => { throw error; }, BASE: "" },
    "@/lib/session": {}, "@/lib/auth-config": {},
  });
  const form = new FormData();
  form.set("case_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  form.set("record_version", "1");
  form.set("reason", "Synthetic reason");
  for (const name of ["startInvestigation", "reopenCase", "updateCase", "resolveConflict", "freezeSnapshot", "correctField", "editReport", "submitReport", "withdrawReport", "cancelRun", "decide"]) {
    const result = await actions[name](form);
    assert.equal(result.ok, false, name);
    assert.equal(result.uncertain, true, name);
  }
});

test("an interrupted document upload returns an uncertain outcome", async () => {
  const error = new ApiRequestError(503, { error: { code: "CONNECTION_ERROR", message: "Outcome may be unknown", correlation_id: "", details: {} } });
  const { uploadDocument } = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} },
    "next/navigation": { redirect: () => {} },
    "@/lib/api": { ApiRequestError, raw: async () => { throw error; }, BASE: "" },
    "@/lib/session": {}, "@/lib/auth-config": {},
  });
  const form = new FormData();
  form.set("case_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  form.set("file", new File(["name,amount\nsample,10\n"], "synthetic.csv", { type: "text/csv" }));
  form.set("document_type", "OTHER");
  form.set("key", "synthetic-upload-key");
  const result = await uploadDocument(form);
  assert.equal(result.ok, false);
  assert.equal(result.uncertain, true);
});

test("an interrupted workflow resume returns an uncertain outcome and safe location", async () => {
  const error = new ApiRequestError(503, { error: { code: "CONNECTION_ERROR", message: "Outcome may be unknown", correlation_id: "", details: {} } });
  const { resumeRun } = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} }, "next/navigation": { redirect: () => {} },
    "@/lib/api": { ApiRequestError, api: async () => { throw error; }, BASE: "" },
    "@/lib/session": {}, "@/lib/auth-config": {},
  });
  const form = new FormData();
  form.set("run_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  form.set("back", "https://evil.example");
  const result = await resumeRun(form);
  assert.equal(result.uncertain, true);
  assert.equal(result.back, "/workflows/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
});

test("case status updates use a bounded request timeout", async () => {
  let options;
  const { updateCase } = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} },
    "next/navigation": { redirect: () => {} },
    "@/lib/api": { ApiRequestError, api: async (_path, init) => { options = init; return {}; }, BASE: "" },
    "@/lib/session": {}, "@/lib/auth-config": {},
  });
  const form = new FormData();
  form.set("case_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  form.set("record_version", "1");
  form.set("reason", "Synthetic reason");
  assert.equal((await updateCase(form)).ok, true);
  assert.equal(options.timeoutMs, 30_000);
});

test("workflow resume only returns an allowed local case or its workflow", async () => {
  const requests = [];
  const { resumeRun } = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} },
    "next/navigation": { redirect: (path) => { throw new Error(`redirect:${path}`); } },
    "@/lib/api": { ApiRequestError, api: async (path) => { requests.push(path); }, BASE: "", raw: () => {} },
    "@/lib/session": {}, "@/lib/auth-config": {},
  });
  const runId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const casePath = "/cases/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  for (const back of ["https://evil.example", "//evil.example", "/\\evil.example", `${casePath}?next=bad`, "/cases/../signin"]) {
    const form = new FormData();
    form.set("run_id", runId);
    form.set("back", back);
    assert.equal((await resumeRun(form)).back, `/workflows/${runId}`);
  }
  const valid = new FormData();
  valid.set("run_id", runId);
  valid.set("back", casePath);
  assert.equal((await resumeRun(valid)).back, casePath);
  assert.equal(requests.length, 6);
  const invalid = new FormData();
  invalid.set("run_id", "../../outside");
  assert.equal((await resumeRun(invalid)).back, "/operations");
  assert.equal(requests.length, 6);
});

test("local sign-in handles unavailable or malformed identity responses without creating a session", async () => {
  const sessions = [];
  const { signIn } = compile("../app/actions.ts", {
    "next/cache": { revalidatePath: () => {} },
    "next/navigation": { redirect: (path) => { throw new Error(`redirect:${path}`); } },
    "@/lib/api": { ApiRequestError, BASE: "http://synthetic.invalid" },
    "@/lib/session": { setSession: async (session) => sessions.push(session) },
    "@/lib/auth-config": { authMode: () => "local" },
  });
  const form = new FormData();
  form.set("subject", "synthetic-analyst");
  const originalFetch = globalThis.fetch;
  try {
    for (const result of [null, new Response("not-json"), new Response("{}"), new Response('{"access_token":"t","expires_in":0}'), new Response("unavailable", { status: 503 })]) {
      globalThis.fetch = async () => { if (result === null) throw new Error("internal service details"); return result; };
      await assert.rejects(() => signIn(form), (error) => {
        assert.ok(error.message.startsWith("redirect:/signin?error="));
        assert.ok(!error.message.includes("internal service details"));
        return true;
      });
      assert.equal(sessions.length, 0);
    }
    globalThis.fetch = async () => new Response('{"access_token":"synthetic-token","expires_in":60}');
    await assert.rejects(() => signIn(form), /redirect:\/$/);
    assert.equal(sessions.length, 1);
    assert.equal(sessions[0].subject, "synthetic-analyst");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
