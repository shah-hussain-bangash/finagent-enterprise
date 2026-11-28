import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { loadComponent } from "./load-component.mjs";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
function loadPage(runResult) {
  const mocks = {
    "@/lib/case-navigation": { caseUpdatePath: (id, _queue, kind, message) => `/cases/${id}?${kind}=${encodeURIComponent(message)}` },
    "@/lib/case-return": { safeCaseReturn: () => null },
    "@/components/CaseAssignmentForm": { CaseAssignmentForm: () => null },
    "@/components/CasePriorityForm": { CasePriorityForm: () => null },
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { reopenCase() {}, resumeRun() {}, startInvestigation() {}, updateCase() {} },
    "@/components/StatusBadge": { StatusBadge: () => null },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Runs unavailable"), Field: ({ children }) => React.createElement("div", null, children), Flash: () => null, Tabs: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async (path) => path.endsWith("/workflows") ? runResult : { data: { case_id: caseId, status: "INVESTIGATING", can_change_block_status: true, alert: { subject_refs: [] }, timeline: [] }, error: null } },
  };
  mocks["@/components/StartInvestigationForm"] = loadComponent("StartInvestigationForm", mocks);
  mocks["@/components/ReopenCaseForm"] = loadComponent("ReopenCaseForm", mocks);
  mocks["@/components/CaseStatusForm"] = loadComponent("CaseStatusForm", mocks);
  mocks["@/components/ResumeWorkflowForm"] = loadComponent("ResumeWorkflowForm", mocks);
  const source = readFileSync(new URL("../app/cases/[id]/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
async function render(runResult, search = {}) {
  return renderToStaticMarkup(await loadPage(runResult)({ params: Promise.resolve({ id: caseId }), searchParams: Promise.resolve(search) }));
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

test("an interrupted case action requires a reload before offering more changes", async () => {
  const html = await render({ data: { items: [] }, error: null }, { error: "CONNECTION_ERROR: Outcome may be unknown" });
  assert.ok(html.includes("Reload case"));
  assert.ok(html.includes(`href="/cases/${caseId}?queue=%2Fcases"`));
  assert.ok(!html.includes("Start investigation"));
  assert.ok(!html.includes("Mark blocked"));
  assert.ok(html.includes("Investigation runs"));
});

test("a confirmed validation error does not hide the case controls", async () => {
  const html = await render({ data: { items: [] }, error: null }, { error: "INVALID: Choose a valid reason" });
  assert.ok(html.includes("Mark blocked"));
  assert.ok(!html.includes("Reload case"));
});

test("an interrupted action also blocks resuming an active investigation", async () => {
  const active = { run_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", status: "WAITING_EVIDENCE", workflow_type: "SYNTHETIC", status_reason: "Waiting", next_action: null, created_at: null };
  const html = await render({ data: { items: [active] }, error: null }, { error: "CONNECTION_ERROR: Outcome may be unknown" });
  assert.ok(html.includes("WAITING_EVIDENCE"));
  assert.ok(!html.includes("Evidence supplied - continue"));
  assert.ok(html.includes("Reload case"));
});
