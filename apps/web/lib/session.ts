// Server-side sessions: local development uses the existing encrypted httpOnly cookie.
// External sign-in uses an opaque httpOnly handle with encrypted token records in Redis. No browser JS receives tokens.
import "server-only";
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { authMode, externalSettings } from "./auth-config";
import { authStore, isSession, SESSION_COOKIE, sessionCookieName, validHandle } from "./auth-store";

import { revokeStoredSession } from "./password-auth";

const COOKIE = "fa_session";

export interface Session {
  token: string;
  subject: string;
  exp: number; // epoch seconds
}

function key(): Buffer {
  const secret = process.env.FINAGENT_WEB_SESSION_SECRET;
  if (!secret || secret.length < 32) {
    throw new Error("FINAGENT_WEB_SESSION_SECRET (>= 32 chars) is required");
  }
  return createHash("sha256").update(secret).digest();
}

function seal(value: Session): string {
  const iv = randomBytes(12);
  const c = createCipheriv("aes-256-gcm", key(), iv);
  const body = Buffer.concat([c.update(JSON.stringify(value), "utf8"), c.final()]);
  return Buffer.concat([iv, c.getAuthTag(), body]).toString("base64url");
}

function open(raw: string): Session | null {
  try {
    const buf = Buffer.from(raw, "base64url");
    const d = createDecipheriv("aes-256-gcm", key(), buf.subarray(0, 12));
    d.setAuthTag(buf.subarray(12, 28));
    const text = Buffer.concat([d.update(buf.subarray(28)), d.final()]).toString("utf8");
    const s = JSON.parse(text) as Session;
    return s.exp * 1000 > Date.now() ? s : null;
  } catch {
    return null; // tampered, wrong key or malformed: treated as signed out
  }
}

export async function getSession(): Promise<Session | null> {
  if (authMode() !== "local") {
    try {
      const cfg = externalSettings();
      const handle = (await cookies()).get(sessionCookieName(cfg.secure, authMode()))?.value;
      if (!handle || !validHandle(handle)) return null;
      const value = await authStore.read("session", handle);
      return isSession(value) ? value : null;
    } catch {
      return null; // store failure fails closed; never accept a legacy development cookie
    }
  }
  const raw = (await cookies()).get(COOKIE)?.value;
  return raw ? open(raw) : null;
}

export async function requireSession(): Promise<Session> {
  const s = await getSession();
  if (!s) redirect("/signin?expired=1");
  return s;
}

export async function setSession(s: Session): Promise<void> {
  if (authMode() !== "local") throw new Error("Development session creation is disabled");
  (await cookies()).set(COOKIE, seal(s), {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: Math.max(0, s.exp - Math.floor(Date.now() / 1000)),
  });
}

export async function clearSession(): Promise<void> {
  const jar = await cookies();
  if (authMode() !== "local") {
    const cfg = externalSettings();
    const name = sessionCookieName(cfg.secure, authMode());
    const handle = jar.get(name)?.value;
    if (handle && validHandle(handle)) {
      // A failed revocation must not be reported as a successful sign-out.
      try { await revokeStoredSession(handle); } catch {
        throw new Error("Sign-out is unavailable. Please try again.");
      }
    }
    jar.set(name, "", { path: "/", maxAge: 0, httpOnly: true, secure: cfg.secure, sameSite: "lax" });
  }
  jar.delete(SESSION_COOKIE);
  jar.delete(COOKIE);
}
