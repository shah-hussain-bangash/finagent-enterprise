import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const runId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const otherId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
function loadPage() {
  const calls = [];
  let authenticated = false;
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [], next_cursor: null }), api: async (path) => {
      assert.ok(authenticated); calls.push(path);
      if (path.endsWith("/workflows")) return { items: [{ run_id: runId, workflow_type: "AML", status: "FAILED", attempt: 2, created_at: "2026-01-01" }] };
      return { run_id: runId, case_id: caseId, workflow_type: "AML", graph_version: "v1", status: "FAILED", status_reason: "Synthetic failure", attempt: 2, max_attempts: 3, next_action: null, blockers: [], steps: [{ node: "gather", status: "FAILED", attempt: 2, error_code: "TEST_ERROR", started_at: "2026-01-01", finished_at: null }] };
    } },
  };
  const source = readFileSync(new URL("../app/workspace/recorded-monitor/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls };
}
test("shows recorded failures, attempts and timing fields", async () => {
  const { page } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, run: runId }) }));
  for (const expected of ["Synthetic failure", "TEST_ERROR", "gather", "2026-01-01", "Model usage and cost are not shown"]) assert.ok(html.includes(expected));
});
test("does not request a run absent from the authorized case list", async () => {
  const { page, calls } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, run: otherId }) }));
  assert.equal(calls.length, 1);
  assert.ok(html.includes("not returned for the selected case"));
});
