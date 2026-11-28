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
function loadPage(matching = true, mismatch = false, missing = false, noContent = false) {
  const calls = [];
  const returnPaths = [];
  const output = { output_id: outputId, capability_id: "AG-KYC", snapshot_id: snapshotId, status: "PARTIAL", created_at: "2026-01-01", lineage: { document_selection: { rule_version: "rule-1", review_date: "2026-01-01", basis: "synthetic", items: [{ document_type: "PASSPORT", selection: "SELECTED", evidence_ref: `ev:${itemId}`, source_ref: "doc-1", processing_status: "QUARANTINED" }] } } };
  const snapshot = { snapshot_id: snapshotId, created_at: "2026-01-01", items: [{ evidence_item_id: itemId, source_type: "DOCUMENT_EXTRACTION", source_ref: "doc-1", source_system: "synthetic", version_no: 1, freshness: "STALE", completeness: "COMPLETE", content_hash: "123456789012", content: { document_type: "PASSPORT", status: "QUARANTINED", fields: [{ field_key: "name", value: "Example", value_state: "PRESENT", validation_status: "VALID" }] } }] };
  if (mismatch) snapshot.items[0].content.document_type = "PROOF_OF_ADDRESS";
  if (missing) snapshot.items = [];
  if (noContent) snapshot.items[0].content = null;
  const mocks = {
    "@/components/ConnectedCaseNav": { ConnectedCaseNav: () => null },
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async (path) => { returnPaths.push(path); } },
    "@/lib/api": { ApiRequestError: class extends Error {}, api: async (path, options) => { calls.push(path); returnPaths.push(options.returnTo); return path.endsWith("/agent-outputs") ? { items: matching ? [output] : [] } : snapshot; } },
  };
  const source = readFileSync(new URL("../app/workspace/snapshot-selection/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls, returnPaths };
}
test("matches selected evidence only within the recorded snapshot", async () => {
  const { page, calls, returnPaths } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, output: outputId }) }));
  for (const expected of [snapshotId, `ev:${itemId}`, "QUARANTINED", "Example", "may differ from current"]) assert.ok(html.includes(expected));
  assert.equal(calls[1], `/api/v1/cases/${caseId}/snapshots/${snapshotId}`);
  assert.deepEqual(returnPaths, Array(3).fill(`/workspace/snapshot-selection?case=${caseId}&output=${outputId}`));
});
test("does not preserve an invalid snapshot selection in the return path", async () => {
  const { page, calls, returnPaths } = loadPage();
  await page({ searchParams: Promise.resolve({ case: caseId, output: "//evil.example" }) });
  assert.deepEqual(returnPaths, [`/workspace/snapshot-selection?case=${caseId}`]);
  assert.equal(calls.length, 0);
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
test("explains unavailable snapshot content without treating it as missing fields", async () => {
  const { page } = loadPage(true, false, false, true);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, output: outputId }) }));
  assert.ok(html.includes("snapshot document content was not returned"));
  assert.ok(!html.includes("No extracted fields were recorded"));
});
