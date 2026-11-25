// Only restore canonical case pages or explicitly supported workspace selections.
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const CASE_PATH = new RegExp(`^/cases/${UUID}(?:/(?:evidence|documents|report|agents|audit))?$`, "i");
const WORKSPACE_PATH = new RegExp(`^/workspace/(?:case-overview|evidence-briefing|recorded-transactions|recorded-risk|recorded-review|recorded-provenance|recorded-monitor|snapshot-selection)\\?case=${UUID}$`, "i");
const SELECTED_PATH = new RegExp(`^/workspace/(?:recorded-provenance\\?case=${UUID}&document=${UUID}|recorded-monitor\\?case=${UUID}&run=${UUID}|snapshot-selection\\?case=${UUID}&output=${UUID}|recorded-review\\?case=${UUID}&version=[1-9][0-9]{0,8})$`, "i");

export function safeCaseReturn(value: unknown): string | null {
  if (typeof value !== "string" || value !== value.trim()) return null;
  return (CASE_PATH.test(value) || WORKSPACE_PATH.test(value) || SELECTED_PATH.test(value))
    ? value
    : null;
}
