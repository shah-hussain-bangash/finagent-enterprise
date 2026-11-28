import { NextResponse } from "next/server";
import { authMode } from "@/lib/auth-config";
import { startDevelopmentSession } from "@/lib/development-session";
import { getSession } from "@/lib/session";

function isLoopback(url: URL): boolean {
  return url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
}

function isWorkbenchLaunch(request: Request): boolean {
  if (!["local", "test"].includes(process.env.FINAGENT_ENVIRONMENT ?? "") || authMode() !== "local") {
    return false;
  }
  const origin = process.env.FINAGENT_WEB_WORKBENCH_ORIGIN;
  const referer = request.headers.get("referer");
  if (!origin || !referer) return false;
  try {
    const workbench = new URL(origin);
    return isLoopback(new URL(request.url)) && isLoopback(workbench) &&
      workbench.origin === origin && new URL(referer).origin === origin;
  } catch {
    return false;
  }
}

export async function GET(request: Request) {
  if (!(await getSession()) && isWorkbenchLaunch(request)) {
    try {
      await startDevelopmentSession("a.analyst1@synthetic", false);
    } catch {
      // The Enterprise dashboard applies the usual sign-in if a session cannot be established.
    }
  }
  return new NextResponse(null, {
    status: 307,
    headers: { Location: "/", "Cache-Control": "no-store" },
  });
}
