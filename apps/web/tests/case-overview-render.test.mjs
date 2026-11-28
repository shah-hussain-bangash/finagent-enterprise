import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
function loadPage(deny = false, gaps = [{ code: "MISSING", severity: "BLOCKING", detail: "Document missing", source_type: "DOCUMENT_EXTRACTION", source_ref: "doc-example" }]) {
  const calls = [];
  const mocks = {
    "@/components/ConnectedCaseNav": { ConnectedCaseNav: () => null },
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/connected-risk": { recordedIndicators: (output) => output.findings },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [{ case_id: caseId, status: "INVESTIGATING" }], next_cursor: "next-token" }), load: async (path) => {
      calls.push(path);
      if (path.endsWith(caseId)) return deny ? { data: null, error: {} } : { data: { case_id: caseId, status: "INVESTIGATING", priority: "HIGH", alert_type: "AML", due_at: null, assigned_analyst_id: null, blocked_reason: null }, error: null };
      if (path.endsWith("/evidence")) return { data: { items: [], gaps }, error: null };
      return { data: { items: [{ output_id: "out1", snapshot_id: "snapshot-1", findings: [{ claim_id: "sample", claim: "Synthetic indicator", support_status: "SUPPORTED" }] }] }, error: null };
    } },
  };
  const source = readFileSync(new URL("../app/workspace/case-overview/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls };
}
test("brings recorded evidence, indicator snapshot and case links together", async () => {
  const { page } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  for (const expected of ["INVESTIGATING", "Document missing", "DOCUMENT_EXTRACTION", "doc-example", "does not by itself identify", "Synthetic indicator", "snapshot-1", `/workspace/recorded-transactions?case=${caseId}`]) assert.ok(html.includes(expected));
  assert.ok(html.includes("Selected"));
  assert.ok(html.includes(`/workspace/case-overview?case=${caseId}&amp;cursor=next-token`));
});
test("preserves the selected case when returning to the first case-list page", async () => {
  const { page } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, cursor: "previous-token" }) }));
  assert.ok(html.includes(`/workspace/case-overview?case=${caseId}`));
  assert.ok(html.includes("First cases"));
});
test("does not request evidence or agent outputs after case access fails", async () => {
  const { page, calls } = loadPage(true);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.equal(calls.length, 1);
  assert.ok(html.includes("Access unavailable"));
});

test("keeps a large checklist visible by default and lets analysts collapse it", async () => {
  const gaps = Array.from({ length: 150 }, (_, index) => ({
    code: "MISSING", severity: "REVIEW", detail: `Synthetic gap ${index + 1}`,
    source_type: "DOCUMENT_EXTRACTION", source_ref: `doc-${index + 1}`,
  }));
  const { page } = loadPage(false, gaps);
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  assert.match(html, /<details open=""><summary>150 checklist gap\(s\) · expand or collapse<\/summary>/);
  assert.ok(html.includes("Synthetic gap 1") && html.includes("Synthetic gap 150"));
  assert.ok(html.includes("doc-150"));
  assert.ok(html.includes(`/workspace/evidence-briefing?case=${caseId}`));
});
