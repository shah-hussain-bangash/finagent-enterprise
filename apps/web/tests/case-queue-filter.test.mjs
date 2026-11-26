import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function page(calls, result = () => ({ items: [], next_cursor: null })) {
  const labels = { RECEIVED: "Received", ASSIGNED: "Assigned", INVESTIGATING: "Investigating", BLOCKED: "Blocked", AWAITING_REVIEW: "Awaiting review", REWORK: "Rework", APPROVED: "Approved", FINALIZING: "Finalizing - external update pending", COMPLETED: "Completed", CANCELLED: "Cancelled" };
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/StatusBadge": { CASE_STATUS_LABEL: labels, StatusBadge: () => null },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, When: () => null },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async (...args) => { calls.push(args); return result(...args); } },
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

test("navigates two full synthetic case pages without losing the status filter", async () => {
  const calls = [];
  const row = (number) => ({
    case_id: `${number.toString(16).padStart(8, "0")}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`,
    status: "INVESTIGATING", priority: "HIGH", alert_type: "SYNTHETIC", source_alert_id: `alert-${number}`,
    created_at: null, updated_at: null,
  });
  const pageSize = 50;
  const queue = page(calls, (_status, cursor) => ({
    items: Array.from({ length: pageSize }, (_, index) => row(index + (cursor ? pageSize : 0))),
    next_cursor: cursor ? null : "next /+",
  }));
  const first = renderToStaticMarkup(await queue({ searchParams: Promise.resolve({ status: "INVESTIGATING" }) }));
  assert.equal((first.match(/alert-\d+/g) ?? []).length, pageSize);
  assert.match(first, /alert-0/);
  assert.doesNotMatch(first, /alert-50(?:<|\s)/);
  assert.ok(first.includes('href="/cases?status=INVESTIGATING&amp;cursor=next+%2F%2B"'));
  const second = renderToStaticMarkup(await queue({ searchParams: Promise.resolve({ status: "INVESTIGATING", cursor: "next /+" }) }));
  assert.equal((second.match(/alert-\d+/g) ?? []).length, pageSize);
  assert.match(second, /alert-50/);
  assert.match(second, /alert-99/);
  assert.ok(second.includes('href="/cases?status=INVESTIGATING"'));
  assert.deepEqual(calls, [["INVESTIGATING", undefined], ["INVESTIGATING", "next /+"]]);
});
