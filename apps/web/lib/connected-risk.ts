/** Narrow projection of recorded AML output. This does not calculate risk. */
export interface RecordedFinding {
  claim_id: string;
  claim_type: string;
  claim: string;
  support_status: string;
  evidence_refs: string[];
}

export interface RecordedOutput {
  output_id: string;
  capability_id: string;
  capability_version: string;
  status: string;
  snapshot_id: string;
  run_id: string | null;
  created_at: string;
  findings: RecordedFinding[];
}

const INDICATOR_IDS = new Set([
  "aml-ind-STRUCTURING_PATTERN",
  "aml-ind-TURNOVER_ABOVE_EXPECTED",
  "aml-ind-LARGE_OUTGOING_TRANSFER",
]);

const CALCULATION_ID = /^(?:cash_below_threshold_count|cash_below_threshold_days|cash_below_threshold_total_[A-Z]{3}|credit_to_expected_ratio_[A-Z]{3}|posted_credit_total_[A-Z]{3}|posted_debit_total_[A-Z]{3}|excluded_pending_count|excluded_reversed_count)$/;

export function recordedIndicators(output: RecordedOutput): RecordedFinding[] {
  if (output.capability_id !== "AG-AML" || !Array.isArray(output.findings)) return [];
  return output.findings.filter(
    (finding) => finding != null && finding.claim_type === "INTERPRETATION" &&
      INDICATOR_IDS.has(finding.claim_id),
  );
}

/** Return only named, explicitly referenced recorded calculations; do not recompute them. */
export function recordedCalculations(output: RecordedOutput): RecordedFinding[] {
  if (output.capability_id !== "AG-AML" || !Array.isArray(output.findings)) return [];
  return output.findings.filter((finding) => {
    if (!finding || finding.claim_type !== "DETERMINISTIC_RESULT" || typeof finding.claim_id !== "string") return false;
    if (!finding.claim_id.startsWith("aml-")) return false;
    const calculation = finding.claim_id.slice(4);
    return CALCULATION_ID.test(calculation) && Array.isArray(finding.evidence_refs) &&
      finding.evidence_refs.includes(`calc:${calculation}`);
  });
}
