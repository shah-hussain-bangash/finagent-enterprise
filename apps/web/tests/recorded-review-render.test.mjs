import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
function loadPage() {
  const calls = [];
  let authenticated = false;
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => React.createElement("p", null, "Access unavailable"), When: ({ iso }) => React.createElement("time", null, iso) },
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "@/lib/api": { ApiRequestError: class extends Error {}, listCases: async () => ({ items: [], next_cursor: null }), api: async (path) => {
      assert.ok(authenticated); calls.push(path);
      if (path.endsWith("/reports")) return { report: { status: "DRAFT", current_version_no: 2 }, versions: [{ version_no: 2, origin: "ANALYST", author_label: "Analyst", created_at: "2026-01-01", valid: false, edit_note: "Reviewed" }] };
      return { version_no: 2, snapshot_id: "snapshot-example", watermark: "DRAFT", validation: { valid: false, problems: [{ code: "MISSING_EVIDENCE" }] }, diff_from_previous: [{ section_id: "summary", change: "MODIFIED", diff: ["-old", "+new"] }] };
    } },
  };
  const source = readFileSync(new URL("../app/workspace/recorded-review/page.tsx", import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return { page: exports.default, calls };
}
test("renders recorded changes, validation problems and snapshot", async () => {
  const { page } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId }) }));
  for (const expected of ["snapshot-example", "MISSING_EVIDENCE", "summary", "+new", "DRAFT"]) assert.ok(html.includes(expected));
  assert.ok(html.includes("#version-details"));
  assert.ok(html.includes('id="version-details"'));
});
test("does not request a report version absent from the case history", async () => {
  const { page, calls } = loadPage();
  const html = renderToStaticMarkup(await page({ searchParams: Promise.resolve({ case: caseId, version: "999" }) }));
  assert.equal(calls.length, 1);
  assert.ok(html.includes("not returned for this case"));
});
