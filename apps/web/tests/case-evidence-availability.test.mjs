import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadPage(evidence, snapshots) {
  const mocks = {
    "@/app/actions": { freezeSnapshot() {}, resolveConflict() {} },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: ({ what }) => React.createElement("p", null, `${what} unavailable`), Flash: () => null, Json: () => null, Tabs: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async (path) => path.endsWith("/snapshots") ? snapshots : evidence },
  };
  const source = readFileSync(new URL("../app/cases/[id]/evidence/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
async function render(evidence, snapshots) {
  return renderToStaticMarkup(await loadPage(evidence, snapshots)({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({}) }));
}
test("distinguishes unavailable history from an empty history and hides snapshot action when evidence is unavailable", async () => {
  const html = await render({ data: null, error: {} }, { data: null, error: {} });
  assert.ok(html.includes("Evidence unavailable"));
  assert.ok(html.includes("Snapshot history unavailable"));
  assert.ok(!html.includes("No snapshots were returned"));
  assert.ok(!html.includes("Freeze current evidence"));
});
test("shows a genuinely empty snapshot history when available", async () => {
  const html = await render({ data: { items: [], gaps: [], conflicts: [], requirements_version: "v1" }, error: null }, { data: { items: [] }, error: null });
  assert.ok(html.includes("No snapshots were returned for this case"));
  assert.ok(html.includes("Freeze current evidence"));
});
test("requires an explicit choice when resolving conflicting evidence", async () => {
  const conflict = { conflict_id: "conflict-1", field_key: "name", value_a: null, value_b: "Example", status: "OPEN" };
  const html = await render({ data: { items: [], gaps: [], conflicts: [conflict], requirements_version: "v1" }, error: null }, { data: { items: [] }, error: null });
  assert.match(html, /<select[^>]*name="resolution"[^>]*required/);
  assert.match(html, /<option value="" disabled="" selected="">Choose a resolution<\/option>/);
  assert.ok(html.includes("value not available"));
});
