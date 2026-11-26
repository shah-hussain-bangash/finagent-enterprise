import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { loadComponent } from "./load-component.mjs";

const require = createRequire(import.meta.url);
const runId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const caseId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
function loadPage() {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { cancelRun() {}, resumeRun() {} },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { ErrorState: () => null, Flash: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async () => ({ data: { run_id: runId, case_id: caseId, status: "HUMAN_REVIEW", workflow_type: "SYNTHETIC", graph_version: "1", attempt: 1, max_attempts: 3, blockers: [], steps: [], history: [] }, error: null }) },
  };
  mocks["@/components/ResumeWorkflowForm"] = loadComponent("ResumeWorkflowForm", mocks);
  mocks["@/components/RecoveryReasonForm"] = loadComponent("RecoveryReasonForm", mocks);
  const source = readFileSync(new URL("../app/workflows/[id]/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}

test("an interrupted run action requires a reload while history remains available", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: runId }), searchParams: Promise.resolve({ error: "CONNECTION_ERROR: Outcome may be unknown" }) }));
  assert.ok(html.includes("Reload run"));
  assert.ok(html.includes(`href="/workflows/${runId}"`));
  assert.ok(!html.includes("Retry from last checkpoint"));
  assert.ok(!html.includes("Cancel run"));
  assert.ok(html.includes("Status history"));
});

test("normal run review still offers retry and cancellation", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: runId }), searchParams: Promise.resolve({}) }));
  assert.ok(html.includes("Retry from last checkpoint"));
  assert.ok(html.includes("Cancel run"));
});
