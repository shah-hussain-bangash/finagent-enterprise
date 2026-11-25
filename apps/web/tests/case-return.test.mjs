import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../lib/case-return.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
new Function("exports", code)(exports);
const { safeCaseReturn } = exports;
const id = "bf69eaa7-86f5-428f-8e1d-bfa2c57420fd";

test("case return accepts only canonical case screens", () => {
  for (const suffix of ["", "/evidence", "/documents", "/report", "/agents", "/audit"]) {
    assert.equal(safeCaseReturn(`/cases/${id}${suffix}`), `/cases/${id}${suffix}`);
  }
});

test("case return accepts only known workspace views with a single case", () => {
  for (const page of ["case-overview", "evidence-briefing", "recorded-transactions", "recorded-risk",
    "recorded-review", "recorded-provenance", "recorded-monitor", "snapshot-selection"]) {
    const path = `/workspace/${page}?case=${id}`;
    assert.equal(safeCaseReturn(path), path);
    assert.equal(safeCaseReturn(`${path}&next=//evil.example`), null);
  }
  assert.equal(safeCaseReturn(`/workspace/unknown?case=${id}`), null);
});

test("case return rejects external, encoded and non-case destinations", () => {
  for (const value of [null, {}, "", "//evil.example", "https://evil.example", `/cases/${id}?next=//evil.example`,
    `/cases/${id}/../admin`, `/cases/${id}%2f..`, `/cases/${id}\\evil`, `/cases/${id}/export`, "/cases/not-a-uuid"]) {
    assert.equal(safeCaseReturn(value), null);
  }
});
