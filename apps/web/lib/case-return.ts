// Only restore canonical case pages; never accept arbitrary redirect targets or query strings.
export function safeCaseReturn(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return /^\/cases\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\/(?:evidence|documents|report|agents|audit))?$/i.test(value)
    ? value
    : null;
}
