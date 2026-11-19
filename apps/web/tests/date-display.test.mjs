import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../components/ui.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
const exports = {};
new Function("require", "exports", "module", code)((id) => id === "next/link" ? { __esModule: true, default: () => null } : require(id), exports, { exports });
test("displays an invalid date without crashing the page", () => {
  assert.ok(renderToStaticMarkup(React.createElement(exports.When, { iso: "not-a-date" })).includes("Invalid date"));
});
test("displays valid dates in UTC", () => {
  assert.ok(renderToStaticMarkup(React.createElement(exports.When, { iso: "2026-10-19T10:31:09Z" })).includes("2026-10-19 10:31:09 UTC"));
});
test("structured details are named and keyboard focusable", () => {
  const html = renderToStaticMarkup(React.createElement(exports.Json, { value: { example: "synthetic" } }));
  assert.ok(html.includes('aria-label="Structured details"'));
  assert.ok(html.includes('tabindex="0"'));
});
