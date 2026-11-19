import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
function loadPage(runResult) {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { reopenCase() {}, resumeRun() {}, startInvestigation() {}, updateCase() {} },
    "@/components/StatusBadge": { StatusBadge: () => null },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Runs unavailable"), Field: ({ children }) => React.createElement("div", null, children), Flash: () => null, Tabs: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async (path) => path.endsWith("/workflows") ? runResult : { data: { case_id: caseId, status: "INVESTIGATING", alert: { subject_refs: [] }, timeline: [] }, error: null } },
  };
  const source = readFileSync(new URL("../app/cases/[id]/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
async function render(runResult) {
  return renderToStaticMarkup(await loadPage(runResult)({ params: Promise.resolve({ id: caseId }), searchParams: Promise.resolve({}) }));
}
test("does not offer to start an investigation if run history is unavailable", async () => {
  const html = await render({ data: null, error: {} });
  assert.ok(html.includes("Runs unavailable"));
  assert.ok(!html.includes("Start investigation"));
});
test("offers to start when run history is available and no run is active", async () => {
  const html = await render({ data: { items: [] }, error: null });
  assert.ok(html.includes("Start investigation"));
});
