import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function render(pathname) {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children, ...props }) => React.createElement("a", { href, ...props }, children) },
    "next/navigation": { usePathname: () => pathname },
  };
  const source = readFileSync(new URL("../components/MainNavigation.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return renderToStaticMarkup(React.createElement(exports.MainNavigation, { items: [{ href: "/", label: "Overview" }, { href: "/cases", label: "Case queue" }, { href: "/workspace", label: "Workspace" }] }));
}
test("marks the matching case section as current, including nested case pages", () => {
  const html = render("/cases/example/evidence");
  assert.match(html, /href="\/cases" aria-current="page"/);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
});
test("only marks overview current on the overview route", () => {
  const html = render("/");
  assert.match(html, /href="\/" aria-current="page"/);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
});
