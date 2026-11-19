import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const baseline = { evaluation_id: "baseline", dataset: "synthetic", dataset_version: "1", config_hash: "config-a", status: "COMPLETED", gate: "PASS_PROPOSED", created_at: "2026-01-01", metrics: { support: { value: 1, threshold: 0.9, threshold_status: "PROPOSED", passed: true, numerator: 1, denominator: 1 } } };
const candidate = { ...baseline, evaluation_id: "candidate", dataset_version: "2", config_hash: "config-b", metrics: { support: { value: null, threshold: 0.9, threshold_status: "PROPOSED", passed: null, omitted: 2 } } };
function loadPage() {
  let authenticated = false;
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "@/lib/api": { ApiRequestError: class extends Error {}, api: async (path) => { assert.ok(authenticated); assert.equal(path, "/api/v1/evaluations"); return { items: [baseline, candidate] }; } },
  };
  const source = readFileSync(new URL("../app/workspace/evaluation-comparison/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("shows recorded metrics, omissions and comparison limitations", async () => {
  const html = renderToStaticMarkup(await loadPage()({ searchParams: Promise.resolve({ baseline: "baseline", candidate: "candidate" }) }));
  for (const expected of ["different datasets or versions", "configuration hashes differ", "omitted 2", "not evaluated", "1/1", "not an acceptance decision"]) assert.ok(html.includes(expected), `Missing: ${expected}`);
});
test("rejects a run not returned for the signed-in account", async () => {
  const html = renderToStaticMarkup(await loadPage()({ searchParams: Promise.resolve({ baseline: "baseline", candidate: "foreign" }) }));
  assert.ok(html.includes("not returned for your account"));
  assert.ok(!html.includes("Recorded metrics by run"));
});
