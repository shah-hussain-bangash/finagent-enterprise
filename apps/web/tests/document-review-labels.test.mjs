import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadPage(versions = []) {
  const field = (id, key) => ({ field_id: id, field_key: key, value: null, value_state: "MISSING", validation_status: "UNVERIFIED", origin: "EXTRACTED" });
  const data = { document_id: "document-1", case_id: "case-1", title: "Sample", document_type: "PASSPORT", versions, fields: [field("field-1", "full_name"), field("field-2", "expiry_date")], field_history: [] };
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { correctField() {} },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, Flash: () => null, Json: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async () => ({ data, error: null }) },
  };
  const source = readFileSync(new URL("../app/documents/[id]/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("each correction form identifies its field in labels and action", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: "document-1" }), searchParams: Promise.resolve({}) }));
  for (const name of ["full_name", "expiry_date"]) {
    assert.ok(html.includes(`New value for ${name}`));
    assert.ok(html.includes(`Reason for correcting ${name}`));
    assert.ok(html.includes(`Save correction for ${name}`));
  }
});
test("keeps the case and document context in navigation", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: "document-1" }), searchParams: Promise.resolve({}) }));
  assert.ok(html.includes('href="/cases/case-1/documents"'));
  assert.ok(html.includes('case=case-1&amp;document=document-1'));
});

test("an interrupted correction leaves extracted fields visible and requires reload", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: "document-1" }), searchParams: Promise.resolve({ error: "CONNECTION_ERROR: Outcome unknown" }) }));
  assert.ok(html.includes("Reload document"));
  assert.ok(html.includes('href="/documents/document-1"'));
  assert.ok(html.includes("full_name") && html.includes("expiry_date"));
  assert.ok(!html.includes("Save correction for full_name"));
});
test("explains a quarantined file without offering a download", async () => {
  const version = { version_id: "version-1", version_no: 1, status: "QUARANTINED", downloadable: false, quarantine: { reason_code: "SCAN", detail: "Review required", owner_role: "ANALYST" }, extraction: null, sha256: "abc", size_bytes: 10 };
  const html = renderToStaticMarkup(await loadPage([version])({ params: Promise.resolve({ id: "document-1" }), searchParams: Promise.resolve({}) }));
  assert.ok(html.includes("not available for download while quarantined"));
  assert.ok(!html.includes("Download original"));
});
