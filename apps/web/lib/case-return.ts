// Only restore canonical case pages or approved workspace views with one case identifier.
export function safeCaseReturn(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return /^(?:\/cases\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\/(?:evidence|documents|report|agents|audit))?|\/workspace\/(?:case-overview|evidence-briefing|recorded-transactions|recorded-risk|recorded-review|recorded-provenance|recorded-monitor|snapshot-selection)\?case=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.test(value)
    ? value
    : null;
}
