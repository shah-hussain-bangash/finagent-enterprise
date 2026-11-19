import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const documentId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const otherId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
function loadPage() {
  const calls = [];
  let authenticated = false;
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [], next_cursor: null }), api: async (path) => {
      assert.ok(authenticated); calls.push(path);
      if (path.endsWith("/documents")) return { items: [{ document_id: documentId, title: "Sample document", document_type: "OTHER", current_version_no: 2, status: "READY" }] };
      if (path.endsWith("/agent-outputs")) return { items: [{ output_id: "o1", capability_id: "AG-KYC", status: "PARTIAL", snapshot_id: "snapshot-example", created_at: "2026-01-01", lineage: { document_selection: { rule_version: "selection-test", review_date: "2026-01-01", basis: "Synthetic rule", items: [{ document_type: "PASSPORT", selection: "SELECTED", evidence_ref: "ev:selected", source_ref: "doc:selected", processing_status: "QUARANTINED" }] } }, findings: [{ claim_id: "k1", claim_type: "GAP", claim: "Synthetic KYC checklist", support_status: "NOT_APPLICABLE", evidence_refs: ["ev:example"] }] }, { output_id: "o3", capability_id: "AG-KYC", status: "PARTIAL", snapshot_id: "snapshot-older", created_at: "2025-01-01", findings: [] }, { output_id: "o2", capability_id: "AG-AML", findings: [] }] };
      return { document_id: documentId, case_id: caseId, title: "Sample document", versions: [{ version_id: "v1", version_no: 1, status: "FAILED", status_reason: "Unreadable", created_at: "2026-01-01", sha256: "abcdef123456789", downloadable: false }], fields: [{ field_id: "f1", field_key: "name", value: "Example", origin: "CORRECTION", validation_status: "VALID", page: 1, row: null, section: null, correction_reason: "Verified manually" }], field_history: [] };
    } },
  };
  const source = readFileSync(new URL("../app/workspace/recorded-provenance/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls };
}
test("shows recorded versions and corrections with historical context", async () => {
  const { page } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, document: documentId }) }));
  for (const expected of ["Unreadable", "CORRECTION", "Verified manually", "page 1", "earlier processing failures", "Synthetic KYC checklist", "ev:example", "snapshot-example", "does not establish which document was selected", "ev:selected", "QUARANTINED", "Selection details were not recorded"]) assert.ok(html.includes(expected));
  assert.ok(html.includes("#document-details"));
  assert.ok(html.indexOf('id="document-details"') < html.indexOf("Recorded KYC checklist findings"));
  assert.ok(html.indexOf("snapshot-example") < html.indexOf("snapshot-older"));
  assert.equal((html.match(/<details class="kyc-history-item" open=""/g) ?? []).length, 1);
});
test("does not request a document absent from the authorized case list", async () => {
  const { page, calls } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, document: otherId }) }));
  assert.equal(calls.length, 2);
  assert.ok(!calls.some((path) => path.endsWith(otherId)));
  assert.ok(html.includes("not returned for the selected case"));
});
