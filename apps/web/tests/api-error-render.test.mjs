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
const { ErrorState } = compile("../components/ui.tsx", {
  "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
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
