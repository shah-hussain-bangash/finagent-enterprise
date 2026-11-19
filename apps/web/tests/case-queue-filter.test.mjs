import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function page(calls) {
  const labels = { RECEIVED: "Received", ASSIGNED: "Assigned", INVESTIGATING: "Investigating", BLOCKED: "Blocked", AWAITING_REVIEW: "Awaiting review", REWORK: "Rework", APPROVED: "Approved", FINALIZING: "Finalizing - external update pending", COMPLETED: "Completed", CANCELLED: "Cancelled" };
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/StatusBadge": { CASE_STATUS_LABEL: labels, StatusBadge: () => null },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, When: () => null },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async (...args) => { calls.push(args); return { items: [], next_cursor: null }; } },
  };
  const source = readFileSync(new URL("../app/cases/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}

test("case status filter uses readable labels while retaining service values", async () => {
  const calls = [];
  const html = renderToStaticMarkup(await page(calls)({ searchParams: Promise.resolve({ status: "AWAITING_REVIEW" }) }));
  assert.deepEqual(calls[0], ["AWAITING_REVIEW", undefined]);
  assert.match(html, /value="AWAITING_REVIEW"[^>]*>Awaiting review/);
});

test("empty later page directs the analyst to the first page with the filter preserved", async () => {
  const calls = [];
  const html = renderToStaticMarkup(await page(calls)({ searchParams: Promise.resolve({ status: "BLOCKED", cursor: "next" }) }));
  assert.deepEqual(calls[0], ["BLOCKED", "next"]);
  assert.ok(html.includes("No cases were returned on this page"));
  assert.ok(html.includes('href="/cases?status=BLOCKED"'));
});
