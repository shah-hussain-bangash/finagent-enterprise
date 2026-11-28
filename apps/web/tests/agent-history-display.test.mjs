import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadPage(items) {
  const mocks = {
    "@/components/CasePageHeading": { CasePageHeading: ({ title }) => React.createElement("h1", null, title) },
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/StatusBadge": { FindingTag: ({ kind }) => React.createElement("span", null, kind) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, Json: () => null, Tabs: () => null, When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async () => ({ data: { items }, error: null }) },
  };
  const source = readFileSync(new URL("../app/cases/[id]/agents/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("keeps the newest output per capability open while retaining earlier history", async () => {
  const output = (id, capability) => ({ output_id: id, capability_id: capability, capability_version: "1", status: "SUCCEEDED", snapshot_id: "snapshot", created_at: id, findings: [], limitations: {}, recommendations: [], validation: {}, lineage: {}, usage: {} });
  const html = renderToStaticMarkup(await loadPage([output("old", "AG-KYC"), output("new", "AG-KYC"), output("aml", "AG-AML")])({ params: Promise.resolve({ id: "case-1" }) }));
  assert.ok(html.indexOf('id="o-aml"') < html.indexOf('id="o-new"'));
  assert.ok(html.indexOf('id="o-new"') < html.indexOf('id="o-old"'));
  assert.equal((html.match(/<details class="panel agent-output" open=""/g) ?? []).length, 2);
  assert.ok(html.includes("id=\"o-old\""));
});
test("retains a larger synthetic output history while expanding only the newest per capability", async () => {
  const items = Array.from({ length: 200 }, (_, index) => ({ output_id: `output-${index}`, capability_id: index % 2 ? "AG-KYC" : "AG-AML", capability_version: "1", status: "SUCCEEDED", snapshot_id: "snapshot", created_at: "2026-01-01", findings: [], limitations: {}, recommendations: [], validation: {}, lineage: {}, usage: {} }));
  const html = renderToStaticMarkup(await loadPage(items)({ params: Promise.resolve({ id: "case-1" }) }));
  assert.ok(html.includes("Showing 200 recorded output(s)"));
  assert.ok(html.includes('id="o-output-0"'));
  assert.ok(html.includes('id="o-output-199"'));
  assert.equal((html.match(/<details class="panel agent-output" open=""/g) ?? []).length, 2);
});
