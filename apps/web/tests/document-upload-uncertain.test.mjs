import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadPage() {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { uploadDocument() {} },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, Flash: () => null, Tabs: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async () => ({ data: { items: [{ document_id: "doc-1", title: "Synthetic document", current_version_no: 1 }] }, error: null }) },
  };
  const source = readFileSync(new URL("../app/cases/[id]/documents/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}

test("an interrupted upload requires reload and retains the document list", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({ error: "CONNECTION_ERROR: Outcome unknown" }) }));
  assert.ok(html.includes("Reload documents"));
  assert.ok(html.includes('href="/cases/case-1/documents"'));
  assert.ok(html.includes("Synthetic document"));
  assert.ok(!html.includes('name="file"'));
});

test("normal document view still offers upload", async () => {
  const html = renderToStaticMarkup(await loadPage()({ params: Promise.resolve({ id: "case-1" }), searchParams: Promise.resolve({}) }));
  assert.ok(html.includes('name="file"'));
  assert.ok(html.includes("Synthetic document"));
});
