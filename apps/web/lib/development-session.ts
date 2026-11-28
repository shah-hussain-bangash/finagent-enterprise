import "server-only";
import { BASE } from "./api";
import { setSession } from "./session";

export async function startDevelopmentSession(subject: string, mfa: boolean): Promise<boolean> {
  const res = await fetch(`${BASE}/api/v1/dev/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subject, mfa }),
    cache: "no-store",
    redirect: "error",
  });
  if (!res.ok) return false;
  const body = (await res.json()) as { access_token: string; expires_in: number };
  await setSession({ token: body.access_token, subject, exp: Math.floor(Date.now() / 1000) + body.expires_in });
  return true;
}
