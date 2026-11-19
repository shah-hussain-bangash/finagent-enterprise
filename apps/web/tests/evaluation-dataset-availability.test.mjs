import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function pageWith(data) {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { startEvaluation() {} },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, Flash: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async () => ({ data, error: null }) },
  };
  const source = readFileSync(new URL("../app/evaluations/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("does not offer to run an evaluation without a returned dataset", async () => {
  const html = renderToStaticMarkup(await pageWith({ datasets: [], items: [] })({ searchParams: Promise.resolve({}) }));
  assert.ok(html.includes("No evaluation datasets were returned"));
  assert.ok(!html.includes("Run evaluation"));
  assert.ok(html.includes("/workspace/evaluation-comparison"));
});
