import "server-only";
import { NextRequest, NextResponse } from "next/server";
import { authMode, externalSettings } from "@/lib/auth-config";
import { sessionCookieName } from "@/lib/auth-store";
import { finishPasswordLogin, passwordLoginEnabled, readPasswordForm } from "@/lib/password-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<NextResponse> {
  let cfg;
  try {
    if (!passwordLoginEnabled()) throw new Error("Disabled");
    cfg = externalSettings();
    if (request.headers.get("origin") !== cfg.origin) throw new Error("Invalid origin");
  } catch {
    return new NextResponse("Sign-in is unavailable.", { status: 400, headers: { "Cache-Control": "no-store" } });
  }
  let response: NextResponse;
  try {
    const form = await readPasswordForm(request);
    const name = sessionCookieName(cfg.secure, authMode());
    const login = await finishPasswordLogin(form.email, form.password, request.cookies.get(name)?.value);
    response = NextResponse.redirect(new URL("/", cfg.origin), 303);
    response.cookies.set(name, login.handle, {
      httpOnly: true, secure: cfg.secure, sameSite: "lax", path: "/",
      maxAge: login.exp - Math.floor(Date.now() / 1000),
    });
    response.cookies.set("fa_session", "", { path: "/", maxAge: 0 });
  } catch {
    response = NextResponse.redirect(new URL("/signin?error=Sign-in%20failed.%20Check%20your%20credentials%20or%20contact%20your%20administrator.", cfg.origin), 303);
  }
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
