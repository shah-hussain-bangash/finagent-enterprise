import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function loadPage(data) {
  const mocks = {
    "@/components/StatusBadge": { CASE_STATUS_LABEL: { RECEIVED: "Received", ASSIGNED: "Assigned", INVESTIGATING: "Investigating", BLOCKED: "Blocked", AWAITING_REVIEW: "Awaiting review", REWORK: "Rework", APPROVED: "Approved", FINALIZING: "Finalizing", COMPLETED: "Completed", CANCELLED: "Cancelled" } },
    "@/lib/session-identity": { sessionIdentity: async () => ({ permissions: ["CASE_READ", "APPROVAL_DECIDE"] }) },
    "@/lib/capability-map": { CAPABILITIES: [], CAPABILITY_DETAILS: [] },
    "next/link": { __esModule: true, default: ({ href, children, className }) => React.createElement("a", { href, className }, children) },
    "@/components/ui": { ErrorState: () => React.createElement("p", null, "Overview unavailable"), When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/api": { load: async (path) => ({ data: path === "/api/v1/cases" ? { items: [], next_cursor: null } : data, error: null }) },
  };
  const source = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}
test("shows grouped missing case statuses as zero and absent optional totals as unavailable", async () => {
  const html = renderToStaticMarkup(await loadPage({ generated_at: "test", scope: "assigned-cases", cases_by_status: { COMPLETED: 1 }, pending_reviews: 0 })());
  assert.match(html, /Assigned cases<\/span><span class="overview-kpi-value">0/);
  assert.match(html, /pending reviews<\/span><span class="overview-kpi-value">0/);
  assert.match(html, /open escalations<\/span><span class="overview-kpi-value">Not available/);
  assert.doesNotMatch(html, /<a[^>]*href="\/escalations"[^>]*><span class="overview-kpi-label">open escalations/);
});
test("explains that the quarantine count has no separate list", async () => {
  const html = renderToStaticMarkup(await loadPage({ generated_at: "test", scope: "assigned-cases", cases_by_status: {}, open_quarantine: 0 })());
  assert.ok(html.includes("A separate quarantine list is not available here"));
  assert.ok(html.includes("Open case queue"));
});
