import { mobileEntryEnabled } from "@/lib/auth-config";
import { revokeStoredSession } from "@/lib/password-auth";
import "server-only";
import { NextRequest, NextResponse } from "next/server";
import { entraSettings } from "@/lib/auth-config";
import { authStore, flowCookieName, sessionCookieName, validHandle } from "@/lib/auth-store";
import { entraConfiguration, finishLogin } from "@/lib/entra";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<NextResponse> {
  if (mobileEntryEnabled()) return new NextResponse("Not found.", { status: 404, headers: { "Cache-Control": "no-store" } });
  let cfg;
  try { cfg = entraSettings(); } catch {
    return new NextResponse("Sign-in is unavailable.", { status: 503, headers: { "Cache-Control": "no-store" } });
  }
  let response: NextResponse;
  try {
    const login = await finishLogin(cfg, await entraConfiguration(cfg), new URL(request.url), request.cookies.get(flowCookieName(cfg.secure))?.value ?? "");
    // Reauthentication rotates the session handle and revokes the previous application session.
    const previous = request.cookies.get(sessionCookieName(cfg.secure))?.value;
    if (previous && validHandle(previous)) {
      try { await revokeStoredSession(previous); } catch {
        await authStore.remove("session", login.handle);
        throw new Error("Session rotation failed");
      }
    }
    response = NextResponse.redirect(new URL("/", cfg.origin), 303);
    response.cookies.set(sessionCookieName(cfg.secure), login.handle, {
      httpOnly: true, secure: cfg.secure, sameSite: "lax", path: "/",
      maxAge: login.session.exp - Math.floor(Date.now() / 1000),
    });
    response.cookies.set("fa_session", "", { path: "/", maxAge: 0 });
  } catch {
    response = NextResponse.redirect(new URL("/signin?error=Sign-in%20failed.%20Please%20try%20again%20or%20contact%20your%20administrator.", cfg.origin), 303);
  }
  response.cookies.set(flowCookieName(cfg.secure), "", {
    path: "/", maxAge: 0, httpOnly: true, secure: cfg.secure, sameSite: "lax",
  });
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
