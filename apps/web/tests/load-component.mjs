import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";

const require = createRequire(import.meta.url);

export function loadComponent(name, mocks) {
  const source = readFileSync(new URL(`../components/${name}.tsx`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports;
}
