import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
for (const [component, action, props, button, refresh] of [
  ["StartInvestigationForm", "startInvestigation", { caseId: "case-1", requestKey: "key" }, "Start investigation", "Refresh case"],
  ["SubmitReportForm", "submitReport", { caseId: "case-1", version: 1, requestKey: "key" }, "Submit version 1", "Refresh report"],
  ["ResolveConflictForm", "resolveConflict", { caseId: "case-1", conflictId: "conflict-1" }, "Record resolution", "Refresh evidence"],
  ["ReopenCaseForm", "reopenCase", { caseId: "case-1", recordVersion: 1 }, "Reopen case", "Reload case"],
  ["CaseStatusForm", "updateCase", { caseId: "case-1", recordVersion: 1, blocked: false }, "Mark blocked", "Reload case"],
  ["ReportEditForm", "editReport", { caseId: "case-1", version: 1, sections: [] }, "Save as new version", "Reload report"],
]) {
  test(`${component} requires a refresh after an uncertain response`, async () => {
    const state = [];
    let index = 0;
    const hooks = { ...React, useState: (initial) => {
      const current = index++;
      if (!(current in state)) state[current] = initial;
      return [state[current], (value) => { state[current] = value; }];
    } };
    const mocks = {
      react: hooks,
      "@/app/actions": { [action]: async () => ({ ok: false, uncertain: true, message: "Outcome unknown" }) },
      "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    };
    const source = readFileSync(new URL(`../components/${component}.tsx`, import.meta.url), "utf8");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
    const exports = {};
    new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
    let element = exports[component](props);
    assert.ok(renderToStaticMarkup(element).includes(button));
    const previousWindow = globalThis.window;
    let navigation = "";
    globalThis.window = { location: { assign: (url) => { navigation = url; } } };
    try { await element.props.action(new FormData()); }
    finally { if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow; }
    index = 0;
    element = exports[component](props);
    const html = renderToStaticMarkup(element);
    assert.ok(html.includes("Outcome unknown"));
    assert.ok(html.includes(refresh));
    assert.ok(!html.includes(`>${button}</button>`));
    if (component === "CaseStatusForm") assert.match(navigation, /\/cases\/case-1\?error=CONNECTION_ERROR/);
    if (component === "ReportEditForm") assert.match(navigation, /\/cases\/case-1\/report\?error=CONNECTION_ERROR/);
  });
}
