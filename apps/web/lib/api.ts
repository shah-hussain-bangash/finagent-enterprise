// Typed API client. Server-side only: the bearer token never reaches the browser bundle.
// Authorization is enforced by the API; the UI only hides what it cannot use (prompt §23).
import "server-only";
import { redirect } from "next/navigation";
import { requireSession } from "@/lib/session";

export type CaseStatus =
  | "RECEIVED" | "ASSIGNED" | "INVESTIGATING" | "BLOCKED" | "AWAITING_REVIEW"
  | "REWORK" | "APPROVED" | "FINALIZING" | "COMPLETED" | "CANCELLED";

export interface CaseSummary {
  case_id: string;
  status: CaseStatus;
  priority: string;
  queue: string;
  alert_type: string;
  source_alert_id: string;
  source_system: string;
  created_at: string;
  updated_at: string;
  record_version: number;
}

export interface ApiError {
  error: { code: string; message: string; correlation_id: string; details: Record<string, unknown> };
}

export class ApiRequestError extends Error {
  constructor(public status: number, public body: ApiError | null) {
    super(typeof body?.error?.message === "string" ? body.error.message : `Request failed (${status})`);
  }
  get code(): string {
    return typeof this.body?.error?.code === "string" ? this.body.error.code : "HTTP_" + this.status;
  }
}

export const BASE = process.env.FINAGENT_API_URL ?? "http://127.0.0.1:8001";

export interface Init {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  idempotencyKey?: string;
  ifMatch?: number;
}

export async function raw(path: string, init: Init = {}): Promise<Response> {
  const session = await requireSession();
  const headers: Record<string, string> = { Authorization: `Bearer ${session.token}`, ...init.headers };
  if (init.json !== undefined) headers["Content-Type"] = "application/json";
  if (init.idempotencyKey) headers["Idempotency-Key"] = init.idempotencyKey;
  if (init.ifMatch !== undefined) headers["If-Match"] = `"${init.ifMatch}"`;
  const method = init.method ?? (init.json !== undefined || init.body ? "POST" : "GET");
  const body = init.json !== undefined ? JSON.stringify(init.json) : init.body;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body,
      cache: "no-store",
    });
  } catch {
    // A failed connection does not establish whether a write reached the service.
    const message = ["GET", "HEAD"].includes(method.toUpperCase())
      ? "The service could not be reached. Please try again."
      : "The connection was interrupted. The outcome may be unknown; check the record before trying again.";
    throw new ApiRequestError(503, { error: { code: "CONNECTION_ERROR", message, correlation_id: "", details: {} } });
  }
  if (res.status === 401) {
    const body = (await res.clone().json().catch(() => null)) as ApiError | null;
    if (body?.error?.code !== "MFA_REQUIRED") redirect("/signin?expired=1"); // session expired
  }
  return res;
}

export async function api<T>(path: string, init: Init = {}): Promise<T> {
  const res = await raw(path, init);
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as ApiError | null;
    throw new ApiRequestError(res.status, body);
  }
  return (await res.json()) as T;
}

/** For pages: returns the error instead of throwing so the page can render an error state. */
export async function load<T>(path: string): Promise<{ data: T; error: null } | { data: null; error: ApiRequestError }> {
  try {
    return { data: await api<T>(path), error: null };
  } catch (e) {
    if (e instanceof ApiRequestError) return { data: null, error: e };
    throw e;
  }
}

export function listCases(status?: CaseStatus, cursor?: string) {
  const q = new URLSearchParams();
  if (status) q.set("status", status);
  if (cursor) q.set("cursor", cursor);
  const qs = q.toString();
  return api<{ items: CaseSummary[]; next_cursor: string | null }>(`/api/v1/cases${qs ? "?" + qs : ""}`);
}
