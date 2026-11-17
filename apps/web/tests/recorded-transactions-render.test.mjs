import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const evidence = { gaps: [], items: [{ evidence_item_id: "e1", source_type: "TRANSACTIONS", source_ref: "source-a", source_system: "synthetic", version_no: 1, freshness: "STALE", completeness: "INCOMPLETE", as_of: null, content: { items: [
  { source_transaction_id: "tx-one", booking_time: "2026-01-01", amount: "42.00", currency: "AED", direction: "CREDIT", status: "POSTED", counterparty_ref: "sample-one" },
  { source_transaction_id: "tx-two", booking_time: "2026-01-02", amount: "10.00", currency: "USD", direction: "DEBIT", status: "PENDING", counterparty_ref: "sample-two" },
] } }] };

function loadPage(response = evidence) {
  let authenticated = false;
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [], next_cursor: null }), api: async (path) => { assert.ok(authenticated); assert.equal(path, `/api/v1/cases/${caseId}/evidence`); return response; } },
  };
  const source = readFileSync(new URL("../app/workspace/recorded-transactions/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}

test("renders only matching recorded transactions with provenance and completeness", async () => {
  const html = renderToStaticMarkup(await loadPage()({ searchParams: Promise.resolve({ case: caseId, q: "sample-one" }) }));
  for (const expected of ["tx-one", "AED", "42.00", "STALE", "INCOMPLETE", "source-a"]) assert.ok(html.includes(expected));
  assert.ok(!html.includes("tx-two"));
});

test("explains absent evidence without claiming no transactions exist", async () => {
  const html = renderToStaticMarkup(await loadPage({ items: [], gaps: [] })({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("does not establish that no transactions exist"));
});

test("filters by direction and status without hiding other statuses by default", async () => {
  const page = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, direction: "DEBIT", status: "PENDING" }) }));
  assert.ok(html.includes("tx-two"));
  assert.ok(!html.includes("tx-one"));
  const all = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(all.includes("tx-one") && all.includes("tx-two"));
  assert.ok(all.indexOf("tx-one") < all.indexOf("tx-two"));
});

test("reports invalid filters instead of silently treating them as valid", async () => {
  const html = renderToStaticMarkup(await loadPage()({ searchParams: Promise.resolve({ case: caseId, status: "INVALID" }) }));
  assert.ok(html.includes("unrecognized filter was ignored"));
});
