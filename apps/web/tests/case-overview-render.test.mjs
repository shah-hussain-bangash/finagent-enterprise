import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
function loadPage(deny = false) {
  const calls = [];
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/connected-risk": { recordedIndicators: (output) => output.findings },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [], next_cursor: null }), load: async (path) => {
      calls.push(path);
      if (path.endsWith(caseId)) return deny ? { data: null, error: {} } : { data: { case_id: caseId, status: "INVESTIGATING", priority: "HIGH", alert_type: "AML", due_at: null, assigned_analyst_id: null, blocked_reason: null }, error: null };
      if (path.endsWith("/evidence")) return { data: { items: [], gaps: [{ code: "MISSING", severity: "BLOCKING", detail: "Document missing" }] }, error: null };
      return { data: { items: [{ output_id: "out1", snapshot_id: "snapshot-1", findings: [{ claim_id: "sample", claim: "Synthetic indicator", support_status: "SUPPORTED" }] }] }, error: null };
    } },
  };
  const source = readFileSync(new URL("../app/workspace/case-overview/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls };
}
test("brings recorded evidence, indicator snapshot and case links together", async () => {
  const { page } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  for (const expected of ["INVESTIGATING", "Document missing", "Synthetic indicator", "snapshot-1", `/workspace/recorded-transactions?case=${caseId}`]) assert.ok(html.includes(expected));
});
test("does not request evidence or agent outputs after case access fails", async () => {
  const { page, calls } = loadPage(true);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.equal(calls.length, 1);
  assert.ok(html.includes("Access unavailable"));
});
