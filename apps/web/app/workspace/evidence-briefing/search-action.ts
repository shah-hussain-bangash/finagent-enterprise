"use server";

import { api, ApiRequestError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import type { EvidenceMatch, EvidenceSearchState } from "@/lib/evidence-search-types";

export async function searchEvidence(_state: EvidenceSearchState, data: FormData): Promise<EvidenceSearchState> {
  await requireSession();
  const caseId = data.get("case_id"), raw = data.get("query");
  const query = typeof raw === "string" ? raw.trim() : "";
  if (typeof caseId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(caseId) || query.length < 3 || query.length > 80) {
    return { error: "Enter between 3 and 80 characters for an accessible case." };
  }
  try {
    const result = await api<{ matches: EvidenceMatch[]; search_limited: boolean }>(`/api/v1/cases/${caseId}/evidence-search`, { method: "POST", json: { query } });
    return { matches: result.matches, limited: result.search_limited };
  } catch (error) {
    if (!(error instanceof ApiRequestError)) throw error;
    return { error: "Evidence search is unavailable or you do not have access. Please try again later." };
  }
}
