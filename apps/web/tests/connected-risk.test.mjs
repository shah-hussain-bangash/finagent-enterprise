import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/connected-risk.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { recordedCalculations, recordedIndicators } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);

const claim = (id, type = "INTERPRETATION") => ({
  claim_id: id, claim_type: type, claim: "Synthetic claim", support_status: "SUPPORTED", evidence_refs: ["calc:sample"],
});
const output = (overrides = {}) => ({ capability_id: "AG-AML", status: "COMPLETED", findings: [], ...overrides });

test("recognizes only the three explicitly identified AML interpretation claims", () => {
  const findings = [
    claim("aml-ind-STRUCTURING_PATTERN"),
    claim("aml-ind-TURNOVER_ABOVE_EXPECTED"),
    claim("aml-ind-LARGE_OUTGOING_TRANSFER"),
    claim("aml-ind-STRUCTURING_PATTERN-extra"),
    claim("aml-ind-STRUCTURING_PATTERN", "FACT"),
    claim("aml-other"),
  ];
  assert.deepEqual(recordedIndicators(output({ findings })), findings.slice(0, 3));
});

test("does not classify claims from other capabilities or absent findings", () => {
  const finding = claim("aml-ind-STRUCTURING_PATTERN");
  assert.deepEqual(recordedIndicators(output({ capability_id: "AG-KYC", findings: [finding] })), []);
  assert.deepEqual(recordedIndicators(output({ findings: null })), []);
  assert.deepEqual(recordedIndicators(output({ findings: [null] })), []);
});

test("preserves recorded support and does not silently discard non-success outputs", () => {
  const finding = { ...claim("aml-ind-TURNOVER_ABOVE_EXPECTED"), support_status: "UNSUPPORTED" };
  assert.deepEqual(recordedIndicators(output({ status: "ESCALATED", findings: [finding] })), [finding]);
});

test("shows only explicitly referenced, recognized recorded calculations", () => {
  const calculation = (id, refs = [`calc:${id}`]) => ({
    claim_id: `aml-${id}`, claim_type: "DETERMINISTIC_RESULT", claim: "Recorded value: 0.",
    support_status: "SUPPORTED", evidence_refs: refs,
  });
  const valid = [calculation("cash_below_threshold_count"), calculation("credit_to_expected_ratio_AED")];
  const invalid = [
    calculation("cash_below_threshold_count", ["calc:other"]),
    calculation("credit_to_expected_ratio_AED_extra"),
    { ...calculation("cash_below_threshold_days"), claim_type: "INTERPRETATION" },
    calculation("not_an_allowed_calculation"),
  ];
  assert.deepEqual(recordedCalculations(output({ findings: [...valid, ...invalid] })), valid);
  assert.deepEqual(recordedCalculations(output({ capability_id: "AG-KYC", findings: valid })), []);
});
