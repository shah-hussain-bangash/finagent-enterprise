import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const summarySource = readFileSync(new URL("../lib/transaction-summary.ts", import.meta.url), "utf8");
const summaryJs = ts.transpileModule(summarySource, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const summary = await import(`data:text/javascript;base64,${Buffer.from(summaryJs).toString("base64")}`);
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
    "@/lib/transaction-summary": summary,
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

test("reports an unavailable evidence response instead of rendering an empty result", async () => {
  for (const response of [null, [], "invalid"]) {
    const html = renderToStaticMarkup(await loadPage(response)({ searchParams: Promise.resolve({ case: caseId }) }));
    assert.ok(html.includes("transaction evidence response is unavailable or malformed"));
    assert.ok(!html.includes("No current transaction evidence is available"));
  }
});

test("discloses missing source and gap lists without claiming evidence is absent", async () => {
  const html = renderToStaticMarkup(await loadPage({ items: null, gaps: null })({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("evidence source list is unavailable or malformed"));
  assert.ok(html.includes("Evidence gap information is unavailable or malformed"));
  assert.ok(!html.includes("No current transaction evidence is available"));
});

test("retains readable sources and gaps while disclosing malformed records", async () => {
  const sample = structuredClone(evidence);
  sample.items.push(null, { source_type: "TRANSACTIONS", evidence_item_id: "broken" });
  sample.gaps.push(null, { source_type: "TRANSACTIONS", detail: "Synthetic stale source" });
  const html = renderToStaticMarkup(await loadPage(sample)({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("2 evidence source record(s) could not be read"));
  assert.ok(html.includes("1 evidence gap record(s) could not be read"));
  assert.ok(html.includes("Synthetic stale source") && html.includes("tx-one"));
  assert.ok(!html.includes("No current transaction evidence is available"));
});

test("distinguishes missing or malformed transaction rows from a valid empty list", async () => {
  for (const content of [null, {}, { items: "invalid" }]) {
    const sample = structuredClone(evidence);
    sample.items[0].content = content;
    const html = renderToStaticMarkup(await loadPage(sample)({ searchParams: Promise.resolve({ case: caseId }) }));
    assert.ok(html.includes("Transaction rows are unavailable or malformed"));
    assert.ok(html.includes("does not establish that there are no transactions"));
    assert.ok(!html.includes("This evidence source contains no transaction rows"));
    assert.ok(!html.includes("No summarizable posted amounts match the filters"));
  }
  const empty = structuredClone(evidence);
  empty.items[0].content.items = [];
  const html = renderToStaticMarkup(await loadPage(empty)({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("This evidence source contains no transaction rows"));
  assert.ok(!html.includes("Transaction rows are unavailable or malformed"));
});

test("discloses malformed records and renders invalid fields without losing valid rows", async () => {
  const sample = structuredClone(evidence);
  sample.items[0].content.items = [
    null, "bad row", {},
    { source_transaction_id: "tx-invalid", booking_time: null, amount: 12.5, currency: { unexpected: true }, direction: "CREDIT", status: "POSTED" },
    evidence.items[0].content.items[0],
  ];
  const html = renderToStaticMarkup(await loadPage(sample)({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("2 malformed transaction record(s) could not be displayed"));
  assert.ok(html.includes("tx-invalid") && html.includes("tx-one"));
  assert.ok(html.includes("Invalid value") && html.includes("12.5 (unverified format)"));
  assert.ok(html.includes("Rows not summarized due to unrecognized status, currency, direction or amount: 2"));
  assert.ok(html.includes("42.00"));
});

test("does not call a source empty when all its records are malformed", async () => {
  const sample = structuredClone(evidence);
  sample.items[0].content.items = [null, "bad row"];
  const html = renderToStaticMarkup(await loadPage(sample)({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(html.includes("2 malformed transaction record(s)"));
  assert.ok(html.includes("No readable transaction records are available"));
  assert.ok(!html.includes("This evidence source contains no transaction rows"));
});

test("filters by direction and status without hiding other statuses by default", async () => {
  const page = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, direction: "DEBIT", status: "PENDING" }) }));
  assert.ok(html.includes("tx-two"));
  assert.ok(!html.includes("tx-one"));
  assert.ok(html.includes("Active filters:"));
  assert.ok(html.includes("Clear filters"));
  assert.ok(html.includes(`/workspace/recorded-transactions?case=${caseId}`));
  const all = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.ok(all.includes("tx-one") && all.includes("tx-two"));
  assert.ok(all.indexOf("tx-one") < all.indexOf("tx-two"));
});

test("reports invalid filters instead of silently treating them as valid", async () => {
  const html = renderToStaticMarkup(await loadPage()({ searchParams: Promise.resolve({ case: caseId, status: "INVALID" }) }));
  assert.ok(html.includes("unrecognized filter was ignored"));
});

test("explains an empty result caused by direction or status filters", async () => {
  const html = renderToStaticMarkup(await loadPage()({ searchParams: Promise.resolve({ case: caseId, direction: "DEBIT", status: "POSTED" }) }));
  assert.ok(html.includes("No recorded transactions match the selected filters"));
  assert.ok(!html.includes("No recorded transactions match this search"));
});

test("reports invalid and out-of-range transaction pages", async () => {
  const page = loadPage();
  for (const selection of [{ page_source: "e1", page: "0" }, { page_source: "missing", page: "2" }, { page: "2" }]) {
    const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, ...selection }) }));
    assert.ok(html.includes("requested transaction page is invalid or its source is unavailable"));
  }
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, page_source: "e1", page: "9" }) }));
  assert.ok(html.includes("requested transaction page is unavailable"));
  assert.ok(html.includes("Showing page 1 of 1 for this source"));
});

test("paginates each source while totals cover all filtered rows and filters are preserved", async () => {
  const many = structuredClone(evidence);
  many.items[0].content.items = Array.from({ length: 60 }, (_, index) => ({ source_transaction_id: `sample-${String(index + 1).padStart(3, "0")}`, booking_time: "2026-01-01", amount: "1.00", currency: "AED", direction: "CREDIT", status: "POSTED", counterparty_ref: "sample" }));
  const page = loadPage(many);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, q: "sample", direction: "CREDIT", status: "POSTED", page_source: "e1", page: "2" }) }));
  assert.ok(html.includes("sample-026"));
  assert.ok(html.includes("sample-050"));
  assert.ok(!html.includes("sample-025"));
  assert.ok(!html.includes("sample-051"));
  assert.ok(html.includes("60.00"));
  assert.ok(html.includes("of 60 filtered transactions"));
  assert.ok(html.includes("q=sample&amp;direction=CREDIT&amp;status=POSTED&amp;page_source=e1&amp;page=3"));
});

test("sums exact posted amounts separately and discloses excluded or unrecognized rows", () => {
  const row = (amount, currency = "AED", direction = "CREDIT", status = "POSTED") => ({ amount, currency, direction, status });
  const result = summary.summarizePosted([row("0.1"), row("0.20"), row("10000000000000000.01"), row("2", "USD"), row("3", "AED", "DEBIT"), row("5", "AED", "CREDIT", "PENDING"), row("5", "AED", "CREDIT", "REVERSED"), row(0.1), row("1e3")]);
  assert.deepEqual(result.totals.find((item) => item.currency === "AED" && item.direction === "CREDIT"), { currency: "AED", direction: "CREDIT", amount: "10000000000000000.31", count: 3 });
  assert.equal(result.totals.length, 3);
  assert.equal(result.excluded, 2);
  assert.equal(result.unrecognized, 2);
});

test("keeps large synthetic sources separate when filtering and paging", async () => {
  const sample = structuredClone(evidence);
  const sourceA = sample.items[0];
  const sourceB = structuredClone(sourceA);
  sourceB.evidence_item_id = "e2";
  sourceB.source_ref = "source-b";
  const rows = (prefix, count, amount) => Array.from({ length: count }, (_, index) => ({
    source_transaction_id: `${prefix}-${String(index + 1).padStart(4, "0")}`,
    booking_time: "2026-01-01", amount, currency: "AED", direction: "CREDIT", status: "POSTED", counterparty_ref: "sample",
  }));
  sourceA.content.items = rows("first", 1000, "1.00");
  sourceB.content.items = rows("second", 700, "2.00");
  sample.items.push(sourceB);
  const html = renderToStaticMarkup(await loadPage(sample)({ searchParams: Promise.resolve({
    case: caseId, q: "sample", direction: "CREDIT", page_source: "e1", page: "2",
  }) }));
  assert.ok(html.includes("first-0026") && html.includes("first-0050"));
  assert.ok(!html.includes("first-0025") && !html.includes("first-0051"));
  assert.ok(html.includes("second-0001") && html.includes("second-0025"));
  assert.ok(!html.includes("second-0026"));
  assert.ok(html.includes("1000.00") && html.includes("1400.00"));
  assert.ok(html.includes("of 1000 filtered transactions") && html.includes("of 700 filtered transactions"));
});
