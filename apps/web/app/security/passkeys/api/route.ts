import "server-only";
import { NextRequest, NextResponse } from "next/server";
import { externalSettings, mobileEntryEnabled } from "@/lib/auth-config";
import { BASE } from "@/lib/api";
import { getSession } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const paths: Record<string, string> = {
  "register-options": "/register/options", "register-finish": "/register/finish",
  "verify-options": "/verify/options", "verify-finish": "/verify/finish", revoke: "/revoke",
};

export async function POST(request: NextRequest): Promise<NextResponse> {
  const reply = (status: number, body: object) => NextResponse.json(body, {
    status, headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
  });
  try {
    if (!mobileEntryEnabled()) return reply(404, { error: "Unavailable" });
    if (request.headers.get("origin") !== externalSettings().origin
        || request.headers.get("content-type")?.split(";")[0] !== "application/json") {
      return reply(400, { error: "Invalid request" });
    }
    const session = await getSession();
    if (!session?.token.startsWith("fa_pwd_")) return reply(401, { error: "Sign in again" });
    const reader = request.body?.getReader();
    if (!reader) return reply(400, { error: "Invalid request" });
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const item = await reader.read();
        if (item.done) break;
        size += item.value.byteLength;
        if (size > 32768) { await reader.cancel(); return reply(413, { error: "Request too large" }); }
        chunks.push(item.value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) return reply(400, { error: "Invalid request" });
    const body = value as Record<string, unknown>;
    if (typeof body.operation !== "string" || !Object.hasOwn(paths, body.operation)
        || Object.keys(body).some((key) => !["operation", "payload"].includes(key))) {
      return reply(400, { error: "Invalid operation" });
    }
    const response = await fetch(BASE + "/api/v1/auth/passkeys" + paths[body.operation], {
      method: "POST", headers: { Authorization: `Bearer ${session.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body.payload ?? {}), cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return reply(response.status, { error: "Request not accepted. Check your session, email verification and enrollment authorization." });
    return reply(200, await response.json() as object);
  } catch {
    return reply(400, { error: "Passkey request could not be completed" });
  }
}
