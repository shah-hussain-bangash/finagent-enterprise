import { mobileEntryEnabled } from "@/lib/auth-config";
import "server-only";
import { NextRequest, NextResponse } from "next/server";
import { entraSettings } from "@/lib/auth-config";
import { authStore, flowCookieName, validHandle } from "@/lib/auth-store";
import { beginLogin, entraConfiguration, requireOrigin } from "@/lib/entra";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (mobileEntryEnabled()) return new NextResponse("Not found.", { status: 404, headers: { "Cache-Control": "no-store" } });
  let cfg;
  try {
    cfg = entraSettings();
    requireOrigin(request.headers.get("origin"), cfg);
  } catch {
    return new NextResponse("Sign-in is unavailable.", { status: 400, headers: { "Cache-Control": "no-store" } });
  }
  try {
    const res = await fetch(`${process.env.FINAGENT_API_URL ?? "http://127.0.0.1:8001"}/api/v1/auth/config`, {
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!res.ok || (await res.json() as { mode?: string }).mode !== "entra") throw new Error("Authentication mode mismatch");
    const previous = request.cookies.get(flowCookieName(cfg.secure))?.value;
    if (previous && validHandle(previous)) await authStore.remove("flow", previous);
    const login = await beginLogin(cfg, await entraConfiguration(cfg));
    const response = NextResponse.redirect(login.location, 303);
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    response.cookies.set(flowCookieName(cfg.secure), login.handle, {
      httpOnly: true, secure: cfg.secure, sameSite: "lax", path: "/", maxAge: 300,
    });
    return response;
  } catch {
    return NextResponse.redirect(new URL("/signin?error=Entra%20sign-in%20is%20unavailable.", cfg.origin), 303);
  }
}
