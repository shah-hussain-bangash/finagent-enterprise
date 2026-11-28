import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const compile = (path, dependencies = {}) => {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true,
  } }).outputText;
  const exports = {};
  const mockRequire = (id) => id in dependencies ? dependencies[id] : require(id);
  new Function("require", "exports", "module", code)(mockRequire, exports, { exports });
  return exports;
};

const selectors = compile("../lib/connected-risk.ts");
const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const indicator = {
  claim_id: "aml-ind-STRUCTURING_PATTERN", claim_type: "INTERPRETATION",
  claim: "Synthetic indicator for analyst review", support_status: "SUPPORTED",
  evidence_refs: ["calc:cash_below_threshold_count"],
};
const output = {
  output_id: "synthetic-output", capability_id: "AG-AML", capability_version: "1",
  status: "SUCCEEDED", snapshot_id: "synthetic-snapshot", run_id: null,
  created_at: "2026-01-01T00:00:00Z", findings: [indicator],
};

test("renders a recorded indicator and its context for a selected case", async () => {
  let authenticated = false;
  const page = compile("../app/workspace/recorded-risk/page.tsx", {
    "@/components/ConnectedCaseNav": { ConnectedCaseNav: () => null },
    "next/link": { __esModule: true, default: ({ children, href }) => React.createElement("a", { href }, children) },
    "@/components/ui": {
      Empty: ({ children }) => React.createElement("p", null, children),
      ErrorState: () => React.createElement("p", null, "Error"),
      When: ({ iso }) => React.createElement("time", null, iso),
    },
    "@/lib/connected-risk": selectors,
    "@/lib/session": { requireSession: async () => { authenticated = true; } },
    "@/lib/api": {
      ApiRequestError: class extends Error {},
      listCases: async () => ({ items: [], next_cursor: null }),
      api: async (path) => {
        assert.equal(authenticated, true);
        assert.equal(path, `/api/v1/cases/${caseId}/agent-outputs`);
        return { items: [output] };
      },
    },
  });
  const html = renderToStaticMarkup(await page.default({ searchParams: Promise.resolve({ case: caseId }) }));
  for (const expected of [indicator.claim_id, indicator.claim, indicator.support_status,
    indicator.evidence_refs[0], output.snapshot_id, "Connected, read-only view"]) {
    assert.ok(html.includes(expected), `Missing rendered content: ${expected}`);
  }
});
