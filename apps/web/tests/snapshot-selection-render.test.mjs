import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const outputId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const snapshotId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const itemId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
function loadPage(matching = true, mismatch = false, missing = false) {
  const calls = [];
  const output = { output_id: outputId, capability_id: "AG-KYC", snapshot_id: snapshotId, status: "PARTIAL", created_at: "2026-01-01", lineage: { document_selection: { rule_version: "rule-1", review_date: "2026-01-01", basis: "synthetic", items: [{ document_type: "PASSPORT", selection: "SELECTED", evidence_ref: `ev:${itemId}`, source_ref: "doc-1", processing_status: "QUARANTINED" }] } } };
  const snapshot = { snapshot_id: snapshotId, created_at: "2026-01-01", items: [{ evidence_item_id: itemId, source_type: "DOCUMENT_EXTRACTION", source_ref: "doc-1", source_system: "synthetic", version_no: 1, freshness: "STALE", completeness: "COMPLETE", content_hash: "123456789012", content: { document_type: "PASSPORT", status: "QUARANTINED", fields: [{ field_key: "name", value: "Example", value_state: "PRESENT", validation_status: "VALID" }] } }] };
  if (mismatch) snapshot.items[0].content.document_type = "PROOF_OF_ADDRESS";
  if (missing) snapshot.items = [];
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { ApiRequestError: class extends Error {}, api: async (path) => { calls.push(path); return path.endsWith("/agent-outputs") ? { items: matching ? [output] : [] } : snapshot; } },
  };
  const source = readFileSync(new URL("../app/workspace/snapshot-selection/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls };
}
test("matches selected evidence only within the recorded snapshot", async () => {
  const { page, calls } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, output: outputId }) }));
  for (const expected of [snapshotId, `ev:${itemId}`, "QUARANTINED", "Example", "may differ from current"]) assert.ok(html.includes(expected));
  assert.equal(calls[1], `/api/v1/cases/${caseId}/snapshots/${snapshotId}`);
});
test("does not fetch a snapshot for an output absent from the authorized case", async () => {
  const { page, calls } = loadPage(false);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, output: outputId }) }));
  assert.equal(calls.length, 1);
  assert.ok(html.includes("No accessible KYC output"));
});
test("warns when selection metadata does not match the snapshot record", async () => {
  const { page } = loadPage(true, true);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, output: outputId }) }));
  assert.ok(html.includes("selection details do not match"));
});
test("warns when the selected evidence is absent from the snapshot", async () => {
  const { page } = loadPage(true, false, true);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, output: outputId }) }));
  assert.ok(html.includes("selected document evidence was not found"));
});
