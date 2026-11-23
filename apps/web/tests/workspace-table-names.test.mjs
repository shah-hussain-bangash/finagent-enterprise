import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../app/workspace/Workspace.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
}).outputText;

function render(view) {
  const hooks = {
    ...React,
    useState: (initial) => [initial === "Dashboard" ? view : typeof initial === "function" ? initial() : initial, () => {}],
    useEffect: () => {},
    useRef: () => ({ current: null }),
  };
  const mocks = {
    react: hooks,
    "next/link": { __esModule: true, default: ({ href, children, ...props }) => React.createElement("a", { href, ...props }, children) },
  };
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return renderToStaticMarkup(React.createElement(exports.default));
}

for (const [view, tableName] of [
  ["Cases", "Sample case list"],
  ["AI evaluation lab", "Sample evaluation results"],
  ["Evidence provenance", "Sample document field comparison"],
  ["AI security testing", "Sample security scenario results"],
  ["Release controls", "Sample release comparison"],
  ["Transactions", "Sample transaction ledger"],
  ["Policies & evaluations", "Sample policy version comparison"],
]) {
  test(`${view} gives its sample table an accessible name`, () => {
    assert.ok(render(view).includes(`<table aria-label="${tableName}">`));
  });
}
