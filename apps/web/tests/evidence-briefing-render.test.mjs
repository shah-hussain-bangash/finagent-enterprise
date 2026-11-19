import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
function loadPage(evidence) {
  let authenticated = false;
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "./EvidenceSearch": { EvidenceSearch: () => React.createElement("p", null, "Search current case evidence") },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [], next_cursor: null }), api: async (path) => { assert.ok(authenticated); assert.equal(path, `/api/v1/cases/${caseId}/evidence`); return evidence; } },
  };
  const source = readFileSync(new URL("../app/workspace/evidence-briefing/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("shows source identifiers, gaps and snapshot limitations", async () => {
  const evidence = { items: [{ evidence_item_id: "evidence-1", source_type: "TRANSACTIONS", source_ref: "source-1", source_system: "synthetic", version_no: 2, freshness: "STALE", completeness: "INCOMPLETE", as_of: null }], gaps: [{ severity: "BLOCKING", code: "MISSING", source_type: "KYC", source_ref: "doc-1", detail: "Document missing" }], conflicts: [], latest_snapshot: { snapshot_id: "snapshot-1", created_at: "2026-01-01" } };
  const html = renderToStaticMarkup(await loadPage(evidence)({ searchParams: Promise.resolve({ case: caseId }) }));
  for (const expected of ["evidence-1", "STALE", "INCOMPLETE", "Document missing", "doc-1", "do not by themselves identify", "snapshot-1", "not represented as snapshot contents", "does not use an AI model"]) assert.ok(html.includes(expected));
});
test("does not claim missing evidence means no risk", async () => {
  const html = renderToStaticMarkup(await loadPage({ items: [], gaps: [], conflicts: [], latest_snapshot: null })({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("not a finding that the case is complete or risk-free"));
});
test("links a returned evidence source to its case record", async () => {
  const evidenceId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const evidence = { items: [{ evidence_item_id: evidenceId, source_type: "TRANSACTIONS", source_ref: "source-1", source_system: "synthetic", version_no: 1, freshness: "FRESH", completeness: "COMPLETE", as_of: null }], gaps: [], conflicts: [], latest_snapshot: null };
  const html = renderToStaticMarkup(await loadPage(evidence)({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes(`/cases/${caseId}/evidence#evidence-${evidenceId}`));
});
