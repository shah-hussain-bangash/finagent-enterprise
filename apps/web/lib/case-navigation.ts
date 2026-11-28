import { safeCaseReturn } from "@/lib/case-return";

/** Retain only validated queue filters when returning to a case after an update. */
export function caseUpdatePath(caseId: string, queue: string, kind: "ok" | "error", message: string): string {
  const safeQueue = safeCaseReturn(queue);
  const params = new URLSearchParams({ [kind]: message });
  if (safeQueue === "/cases" || safeQueue?.startsWith("/cases?")) params.set("queue", safeQueue);
  return `/cases/${encodeURIComponent(caseId)}?${params}`;
}
