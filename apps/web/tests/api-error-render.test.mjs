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
const { ApiRequestError, raw } = compile("../lib/api.ts", {
  "server-only": {}, "next/navigation": { redirect: (path) => { throw new Error(`redirect:${path}`); } },
  "@/lib/session": { requireSession: async () => ({ token: "synthetic" }) },
});
const { ErrorState, Flash } = compile("../components/ui.tsx", {
  "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
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
  for (const name of ["startInvestigation", "reopenCase", "resolveConflict", "submitReport", "decide"]) {
    const result = await actions[name](form);
    assert.equal(result.ok, false, name);
    assert.equal(result.uncertain, true, name);
  }
});

test("workflow resume only redirects to an allowed local case or its workflow", async () => {
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
    await assert.rejects(() => resumeRun(form), (error) => error.message.startsWith(`redirect:/workflows/${runId}?ok=`));
  }
  const valid = new FormData();
  valid.set("run_id", runId);
  valid.set("back", casePath);
  await assert.rejects(() => resumeRun(valid), (error) => error.message.startsWith(`redirect:${casePath}?ok=`));
  assert.equal(requests.length, 6);
  const invalid = new FormData();
  invalid.set("run_id", "../../outside");
  await assert.rejects(() => resumeRun(invalid), (error) => error.message.startsWith("redirect:/operations?error="));
  assert.equal(requests.length, 6);
});
