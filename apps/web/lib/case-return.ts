// Only restore canonical case pages, validated queue filters, or explicitly supported workspace selections.
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const CASE_PATH = new RegExp(`^/cases/${UUID}(?:/(?:evidence|documents|report|agents|audit))?$`, "i");
const REPORT_VERSION_PATH = new RegExp(`^/cases/${UUID}/report\\?v=[1-9][0-9]{0,8}$`, "i");
const SNAPSHOT_PATH = new RegExp(`^/cases/${UUID}/evidence/${UUID}(?:\\?item=${UUID})?$`, "i");
const DOCUMENT_BASE = new RegExp(`^/documents/${UUID}$`, "i");
const CASE_DOCUMENTS_BASE = new RegExp(`^/cases/${UUID}/documents$`, "i");
const SEGMENTS_PATH = new RegExp(`^/documents/${UUID}/versions/${UUID}/segments(?:\\?offset=(?:0|[1-9][0-9]{0,6}))?$`, "i");
const WORKSPACE_PATH = new RegExp(`^/workspace/(?:case-overview|evidence-briefing|recorded-transactions|recorded-risk|recorded-review|recorded-provenance|recorded-monitor|snapshot-selection)\\?case=${UUID}$`, "i");
const SELECTED_PATH = new RegExp(`^/workspace/(?:recorded-provenance\\?case=${UUID}&document=${UUID}|recorded-monitor\\?case=${UUID}&run=${UUID}|snapshot-selection\\?case=${UUID}&output=${UUID}|recorded-review\\?case=${UUID}&version=[1-9][0-9]{0,8})$`, "i");
const PROVENANCE_PAGE_PATH = new RegExp(`^/workspace/recorded-provenance\\?case=${UUID}(?:&document=${UUID})?&doc_offset=(?:0|[1-9][0-9]{0,5}|1000000)$`, "i");
const QUEUE_STATUSES = new Set(["RECEIVED", "ASSIGNED", "INVESTIGATING", "BLOCKED", "AWAITING_REVIEW", "REWORK", "APPROVED", "FINALIZING", "COMPLETED", "CANCELLED"]);
const QUEUE_PRIORITIES = new Set(["LOW", "NORMAL", "HIGH", "URGENT"]);
const DOCUMENT_STATUSES = new Set(["RECEIVED", "QUARANTINED", "STORED", "PARSING", "PARSED", "OCR_UNAVAILABLE", "PARSE_FAILED"]);

function safeDocumentListReturn(value: string): string | null {
  if (value.length > 256) return null;
  if (CASE_DOCUMENTS_BASE.test(value)) return value;
  const split = value.split("?");
  if (split.length !== 2 || !CASE_DOCUMENTS_BASE.test(split[0]!)) return null;
  const query = new URLSearchParams(split[1]);
  const keys = [...query.keys()];
  if (!keys.length || keys.some((key) => !["offset", "status", "document_type"].includes(key) || query.getAll(key).length !== 1)) return null;
  const offset = query.get("offset"), status = query.get("status"), type = query.get("document_type");
  if ((offset !== null && !/^(0|[1-9][0-9]{0,5}|1000000)$/.test(offset))
    || (status !== null && !DOCUMENT_STATUSES.has(status))
    || (type !== null && !/^[A-Z0-9_]{1,48}$/.test(type))) return null;
  const safe = new URLSearchParams();
  if (status) safe.set("status", status);
  if (type) safe.set("document_type", type);
  if (offset && offset !== "0") safe.set("offset", offset);
  return safe.size ? `${split[0]}?${safe}` : split[0]!;
}

function safeDocumentReviewReturn(value: string): string | null {
  if (value.length > 700) return null;
  const split = value.split("?");
  if (split.length > 2 || !DOCUMENT_BASE.test(split[0]!)) return null;
  if (split.length === 1) return value;
  const query = new URLSearchParams(split[1]);
  const keys = [...query.keys()];
  if (!keys.length || keys.some((key) => !["fields_page", "returnTo"].includes(key) || query.getAll(key).length !== 1)) return null;
  const page = query.get("fields_page"), back = query.get("returnTo");
  const safeBack = back ? safeDocumentListReturn(back) : null;
  if ((page !== null && !/^[1-9][0-9]{0,5}$/.test(page)) || (back !== null && !safeBack)) return null;
  const safe = new URLSearchParams();
  if (page && page !== "1") safe.set("fields_page", page);
  if (safeBack) safe.set("returnTo", safeBack);
  return safe.size ? `${split[0]}?${safe}` : split[0]!;
}

function safeQueueReturn(value: string): string | null {
  if (value === "/cases") return value;
  if (value.length > 256 || !value.startsWith("/cases?")) return null;
  const query = new URLSearchParams(value.slice("/cases?".length));
  const keys = [...query.keys()];
  if (!keys.length || keys.some(key => !["status", "priority", "case_ref"].includes(key) || query.getAll(key).length !== 1)) return null;
  const status = query.get("status"), priority = query.get("priority"), caseRef = query.get("case_ref");
  if ((status !== null && !QUEUE_STATUSES.has(status)) || (priority !== null && !QUEUE_PRIORITIES.has(priority)) || (caseRef !== null && !/^[0-9a-f]{8}$/i.test(caseRef))) return null;
  const safe = new URLSearchParams();
  if (status) safe.set("status", status);
  if (priority) safe.set("priority", priority);
  if (caseRef) safe.set("case_ref", caseRef.toLowerCase());
  return `/cases?${safe}`;
}

export function safeCaseReturn(value: unknown): string | null {
  if (typeof value !== "string" || value !== value.trim()) return null;
  const queue = safeQueueReturn(value);
  if (queue) return queue;
  const documents = safeDocumentListReturn(value);
  if (documents) return documents;
  const document = safeDocumentReviewReturn(value);
  if (document) return document;
  return (CASE_PATH.test(value) || REPORT_VERSION_PATH.test(value) || SNAPSHOT_PATH.test(value) || SEGMENTS_PATH.test(value) || WORKSPACE_PATH.test(value) || SELECTED_PATH.test(value) || PROVENANCE_PAGE_PATH.test(value))
    ? value
    : null;
}
