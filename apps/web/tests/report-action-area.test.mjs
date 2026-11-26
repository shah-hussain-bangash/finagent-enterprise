import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

test("paused report actions expose the native disabled state", () => {
  const source = readFileSync(new URL("../components/ReportActionGuard.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {}, require = createRequire(import.meta.url);
  const react = { ...React, useContext: () => true };
  new Function("require", "exports", "module", code)((id) => id === "react" ? react : require(id), exports, { exports });
  const html = renderToStaticMarkup(exports.ReportActionArea({ children: React.createElement("button", null, "Export") }));
  assert.match(html, /<fieldset disabled="" aria-label="Report actions"/);
});
