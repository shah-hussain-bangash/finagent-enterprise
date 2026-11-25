import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { loadComponent } from "./load-component.mjs";

const require = createRequire(import.meta.url);
function pageWithValidation(valid, calls = [], history = false, destinations = []) {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { createExport() {}, editReport() {}, submitReport() {}, withdrawReport() {} },
    "@/components/StatusBadge": { FindingTag: () => null },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ReportActionGuard": { ReportActionLock: React.createContext(() => {}), ReportActionGuard: ({ children }) => children, ReportActionArea: ({ children }) => children },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, Flash: () => null, Tabs: () => null, When: () => null },
    "@/lib/session": { requireSession: async (destination) => { destinations.push(destination); } },
    "@/lib/api": { load: async (path, destination) => { calls.push(path); destinations.push(destination); return path.includes("/versions/") ? { data: { version_no: 1, current_version_no: history ? 2 : 1, sections: [], claims: [], validation: { valid, problems: [{ code: "MISSING_EVIDENCE" }] } }, error: null } : { data: { report: { status: history ? "SUBMITTED" : "DRAFT", current_version_no: history ? 2 : 1 }, versions: history ? [{ version_no: 1 }] : [] }, error: null }; } },
  };
  mocks["@/components/SubmitReportForm"] = loadComponent("SubmitReportForm", mocks);
  mocks["@/components/ReportEditForm"] = loadComponent("ReportEditForm", mocks);
  mocks["@/components/RecoveryReasonForm"] = loadComponent("RecoveryReasonForm", mocks);
  const source = readFileSync(new URL("../app/cases/[id]/report/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
async function render(valid) {
  return renderToStaticMarkup(await pageWithValidation(valid)({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({}) }));
}
test("does not offer submission while the displayed report has blocking validation problems", async () => {
  const html = await render(false);
  assert.ok(html.includes("MISSING_EVIDENCE"));
  assert.ok(html.includes("Resolve the validation problems"));
  assert.ok(!html.includes("Submit version 1 for independent review"));
});
test("does not offer to withdraw the current submission while viewing an older version", async () => {
  const destinations = [];
  const html = renderToStaticMarkup(await pageWithValidation(true, [], true, destinations)({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({ v: "1" }) }));
  assert.ok(html.includes("You are viewing an earlier version"));
  assert.ok(html.includes('href="/cases/case-1/report"'));
  assert.ok(!html.includes("Withdraw current submission"));
  assert.deepEqual(destinations, Array(3).fill("/cases/case-1/report?v=1"));
});
test("offers submission for a valid current draft", async () => {
  const html = await render(true);
  assert.ok(html.includes("Submit version 1 for independent review"));
});
test("does not fetch a malformed or unlisted report version", async () => {
  for (const v of ["NaN", "999"]) {
    const calls = [];
    const html = renderToStaticMarkup(await pageWithValidation(true, calls)({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({ v }) }));
    assert.equal(calls.length, 1);
    assert.ok(html.includes("selected report version was not returned"));
  }
});

test("an interrupted report action requires reload while recorded claims remain visible", async () => {
  const html = renderToStaticMarkup(await pageWithValidation(true)({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({ error: "CONNECTION_ERROR: Outcome unknown" }) }));
  assert.ok(html.includes("Reload report"));
  assert.ok(html.includes('href="/cases/case-1/report"'));
  assert.ok(html.includes("Claims and evidence links"));
  assert.ok(!html.includes("Save as new version"));
  assert.ok(!html.includes("Submit version 1 for independent review"));
  assert.ok(!html.includes("Export this version"));
});
