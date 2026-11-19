import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadPage(calls) {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async (path) => { calls.push(path); return { data: { items: [] }, error: null }; } },
  };
  const source = readFileSync(new URL("../app/approvals/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("invalid approval status is explained and falls back to pending", async () => {
  const calls = [];
  const html = renderToStaticMarkup(await loadPage(calls)({ searchParams: Promise.resolve({ status: "UNKNOWN" }) }));
  assert.deepEqual(calls, ["/api/v1/approvals?status=PENDING"]);
  assert.ok(html.includes("requested status was not recognized"));
});
test("valid approval status is sent to the service", async () => {
  const calls = [];
  await loadPage(calls)({ searchParams: Promise.resolve({ status: "APPROVED" }) });
  assert.deepEqual(calls, ["/api/v1/approvals?status=APPROVED"]);
});
