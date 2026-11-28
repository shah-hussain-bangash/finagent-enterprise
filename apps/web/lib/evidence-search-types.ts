export interface EvidenceMatch {
  evidence_ref: string; source_type: string; source_ref: string; version_no: number;
  freshness: string; completeness: string; as_of: string | null; field_path: string;
  excerpt: string; excerpt_truncated: boolean;
}
export interface EvidenceSearchState { error?: string; matches?: EvidenceMatch[]; limited?: boolean; sessionExpired?: boolean }
