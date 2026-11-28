import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { loadComponent } from "./load-component.mjs";

const require = createRequire(import.meta.url);
function loadPage(data) {
  const mocks = {
    "@/components/EvidenceReference": { EvidenceReference: ({ reference }) => reference },
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { decide() {} },
    "@/components/StatusBadge": { FindingTag: () => null },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { ErrorState: () => null, Flash: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async () => ({ data, error: null }) },
  };
  mocks["@/components/DecisionForm"] = loadComponent("DecisionForm", mocks);
  const source = readFileSync(new URL("../app/approvals/[id]/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
async function render(extra) {
  const data = { case_id: "case-1", status: "PENDING", evidence_drift: [], ...extra };
  return renderToStaticMarkup(await loadPage(data)({ params: Promise.resolve({ id: "review-1" }), searchParams: Promise.resolve({}) }));
}
test("does not offer a decision when the bound review context is missing", async () => {
  const html = await render({});
  assert.ok(html.includes("evidence snapshot was not returned"));
  assert.ok(html.includes("report version was not returned"));
  assert.ok(!html.includes("Record decision"));
});
test("shows snapshot conflicts and report limitations before offering a decision", async () => {
  const html = await render({ snapshot: { version_no: 1, completeness_status: "INCOMPLETE", open_conflicts: 2, gaps: [] }, report: { version_no: 1, sections: [], claims: [], limitations: { evidence: ["Source is stale"] } } });
  assert.ok(html.includes("Open conflicts recorded in this snapshot: 2"));
  assert.ok(html.includes("Source is stale"));
  assert.ok(html.includes("Record decision"));
});
