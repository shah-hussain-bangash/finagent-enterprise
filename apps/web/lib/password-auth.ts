import "server-only";
import { authMode, externalSettings } from "./auth-config";
import { authStore, newHandle, validHandle, type AuthStore } from "./auth-store";

export function passwordLoginEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.FINAGENT_WEB_PASSWORD_LOGIN_ENABLED === "true" && authMode(env) !== "local";
}

const apiUrl = (): string => process.env.FINAGENT_API_URL ?? "http://127.0.0.1:8001";

export async function passwordLoginAvailable(): Promise<boolean> {
  if (!passwordLoginEnabled()) return false;
  try {
    const response = await fetch(`${apiUrl()}/api/v1/auth/password/config`, {
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) return false;
    const value: unknown = await response.json();
    return !!value && typeof value === "object" && "enabled" in value && value.enabled === true;
  } catch {
    // Optional password configuration failure does not disable provider sign-in.
    return false;
  }
}

export async function revokePasswordToken(token: string): Promise<void> {
  if (!token.startsWith("fa_pwd_")) return;
  const response = await fetch(`${apiUrl()}/api/v1/auth/password/logout`, {
    method: "POST", headers: { Authorization: `Bearer ${token}` },
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
  });
  if (response.status !== 204) throw new Error("Session revocation unavailable");
}

export async function revokeStoredSession(handle: string, store: AuthStore = authStore): Promise<void> {
  if (!validHandle(handle)) return;
  const value = await store.read("session", handle);
  if (value && typeof value === "object" && "token" in value && typeof value.token === "string") {
    await revokePasswordToken(value.token);
  }
  await store.remove("session", handle);
}

export async function finishPasswordLogin(
  email: string, password: string, previous: string | undefined, store: AuthStore = authStore,
): Promise<{ handle: string; exp: number }> {
  if (!passwordLoginEnabled()) throw new Error("Password sign-in disabled");
  externalSettings(); // validates origin, API transport, Redis and primary provider
  const response = await fetch(`${apiUrl()}/api/v1/auth/password/login`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }), cache: "no-store", redirect: "error",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("Sign-in failed");
  const value: unknown = await response.json();
  if (!value || typeof value !== "object") throw new Error("Invalid login response");
  const login = value as Record<string, unknown>;
  const now = Math.floor(Date.now() / 1000);
  if (typeof login.access_token !== "string" || !/^fa_pwd_[A-Za-z0-9_-]{43}$/.test(login.access_token)
      || typeof login.expires_at !== "number" || !Number.isInteger(login.expires_at)
      || login.expires_at <= now || login.expires_at > now + 1800
      || typeof login.subject !== "string" || !login.subject || login.subject.length > 300
      || login.mfa_satisfied !== false || login.token_type !== "Bearer") {
    throw new Error("Invalid login response");
  }
  const token = login.access_token, exp = login.expires_at, handle = newHandle();
  try {
    const identity = await fetch(`${apiUrl()}/api/v1/auth/session`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(10000),
    });
    if (!identity.ok) throw new Error("Identity unavailable");
    await store.put("session", handle, { token, subject: login.subject, exp }, exp - now);
    if (previous && validHandle(previous)) await revokeStoredSession(previous, store);
    return { handle, exp };
  } catch {
    await store.remove("session", handle);
    await revokePasswordToken(token);
    throw new Error("Sign-in failed");
  }
}

export async function readPasswordForm(request: Request): Promise<{ email: string; password: string; returnTo?: string }> {
  const contentType = (request.headers.get("content-type") ?? "").split(";")[0];
  if (contentType !== "application/x-www-form-urlencoded" || !request.body) throw new Error("Invalid form");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      size += item.value.byteLength;
      if (size > 4096) { await reader.cancel(); throw new Error("Form too large"); }
      chunks.push(item.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  const form = new URLSearchParams(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  if (form.getAll("email").length !== 1 || form.getAll("password").length !== 1
      || form.getAll("returnTo").length > 1
      || [...form.keys()].some((key) => key !== "email" && key !== "password" && key !== "returnTo")) throw new Error("Invalid form");
  const email = form.get("email") ?? "", password = form.get("password") ?? "";
  if (email.length < 1 || email.length > 254 || password.length < 1 || password.length > 128) throw new Error("Invalid form");
  const returnTo = form.get("returnTo");
  return returnTo === null ? { email, password } : { email, password, returnTo };
}
