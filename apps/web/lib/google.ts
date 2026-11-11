import "server-only";
import { logAuthDiagnostic } from "./auth-diagnostics";
import * as oidc from "openid-client";
import { timingSafeEqual } from "node:crypto";
import type { GoogleSettings } from "./auth-config";
import { authStore, newHandle, validHandle, type AuthStore, type LoginFlow, type StoredSession } from "./auth-store";

let configuration: Promise<oidc.Configuration> | undefined;
export async function googleConfiguration(cfg: GoogleSettings): Promise<oidc.Configuration> {
  if (!configuration) {
    configuration = oidc.discovery(new URL(cfg.issuer), cfg.clientId, {
      id_token_signed_response_alg: "RS256",
    }, oidc.ClientSecretPost(cfg.clientSecret), {
      timeout: 10,
      execute: [oidc.enableNonRepudiationChecks],
    }).then((config) => {
      const metadata = config.serverMetadata();
      // Do not send credentials to endpoints on any other host, even via metadata.
      for (const [endpoint, expected] of [
        [metadata.authorization_endpoint, "https://accounts.google.com/o/oauth2/v2/auth"],
        [metadata.token_endpoint, "https://oauth2.googleapis.com/token"],
        [metadata.jwks_uri, "https://www.googleapis.com/oauth2/v3/certs"],
      ]) {
        if (endpoint !== expected) throw new Error("Unexpected Google endpoint");
      }
      if (metadata.issuer !== cfg.issuer) throw new Error("Unexpected Google issuer");
      return config;
    }).catch(() => {
      configuration = undefined;
      throw new Error("Google discovery failed");
    });
  }
  return configuration;
}

export function sameValue(a: string, b: string): boolean {
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function requireOrigin(origin: string | null, cfg: GoogleSettings): void {
  if (origin !== cfg.origin) throw new Error("Invalid sign-in origin");
}

export async function beginLogin(
  cfg: GoogleSettings, config: oidc.Configuration, store: AuthStore = authStore,
): Promise<{ location: string; handle: string }> {
  let stage = "random_values";
  try {
    const handle = newHandle();
    const flow: LoginFlow = {
      state: oidc.randomState(), nonce: oidc.randomNonce(), verifier: oidc.randomPKCECodeVerifier(),
      exp: Math.floor(Date.now() / 1000) + 300,
    };
    stage = "pkce_challenge";
    const challenge = await oidc.calculatePKCECodeChallenge(flow.verifier);
    stage = "authorization_url";
    const location = oidc.buildAuthorizationUrl(config, {
      redirect_uri: cfg.redirectUri,
      scope: "openid email profile",
      response_type: "code", response_mode: "query",
      code_challenge_method: "S256", code_challenge: challenge,
      state: flow.state, nonce: flow.nonce,
    }).href;
    stage = "flow_store";
    await store.put("flow", handle, flow, 300);
    return { location, handle };
  } catch (error: unknown) {
    logAuthDiagnostic("google_flow", stage, error);
    throw error;
  }
}

interface ApiIdentity { subject: string; issuer: string; display_name: string }
export type CheckApi = (token: string) => Promise<ApiIdentity>;

export async function checkApiIdentity(token: string): Promise<ApiIdentity> {
  const base = process.env.FINAGENT_API_URL ?? "http://127.0.0.1:8001";
  const res = await fetch(`${base}/api/v1/auth/session`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error("API authentication rejected");
  return await res.json() as ApiIdentity;
}

export async function finishLogin(
  cfg: GoogleSettings, config: oidc.Configuration, requestUrl: URL, handle: string,
  checkApi: CheckApi = checkApiIdentity, store: AuthStore = authStore,
): Promise<{ handle: string; session: StoredSession }> {
  if (!validHandle(handle) || requestUrl.pathname !== "/auth/google/callback" || requestUrl.search.length > 16384) {
    throw new Error("Invalid login transaction");
  }
  const value = await store.read("flow", handle, true);
  if (!value || typeof value !== "object") throw new Error("Login transaction expired");
  const flow = value as LoginFlow;
  if (typeof flow.exp !== "number" || flow.exp <= Date.now() / 1000
      || typeof flow.state !== "string" || typeof flow.nonce !== "string" || typeof flow.verifier !== "string") {
    throw new Error("Invalid login transaction");
  }
  // Reject duplicates and provider errors before exchanging a code. Error descriptions are never displayed.
  if (requestUrl.searchParams.has("error") || requestUrl.searchParams.getAll("code").length !== 1
      || requestUrl.searchParams.getAll("state").length !== 1
      || !sameValue(requestUrl.searchParams.get("state") ?? "", flow.state)) throw new Error("Invalid callback");
  const callback = new URL(cfg.redirectUri);
  callback.search = requestUrl.search;
  const tokens = await oidc.authorizationCodeGrant(config, callback, {
    expectedState: flow.state, expectedNonce: flow.nonce, pkceCodeVerifier: flow.verifier,
    idTokenExpected: true,
  });
  const claims = tokens.claims();
  const now = Math.floor(Date.now() / 1000);
  if (!claims || claims.iss !== cfg.issuer || claims.aud !== cfg.clientId
      || (claims.azp !== undefined && claims.azp !== cfg.clientId)
      || typeof claims.sub !== "string" || !claims.sub || claims.sub.length > 255
      || typeof claims.exp !== "number" || !Number.isInteger(claims.exp)
      || typeof claims.iat !== "number" || !Number.isInteger(claims.iat)
      || claims.iat > now + 30 || claims.exp <= now || claims.exp <= claims.iat
      || claims.exp - claims.iat > 3660
      || !tokens.id_token || tokens.id_token.length > 24576) {
    throw new Error("Invalid authentication response");
  }
  // Google ID credentials are independently verified by our backend for this exact web client.
  // Google API access tokens are neither sent to FinAgent nor retained.
  const identity = await checkApi(tokens.id_token);
  if (identity.subject !== claims.sub || identity.issuer !== cfg.issuer) throw new Error("Identity mismatch");
  const exp = Math.min(claims.exp, now + 3600);
  const ttl = exp - now;
  const session: StoredSession = { token: tokens.id_token, subject: identity.subject, exp };
  const sessionHandle = newHandle();
  await store.put("session", sessionHandle, session, ttl);
  // Only the ID credential is encrypted server-side. Access/refresh tokens are discarded.
  return { handle: sessionHandle, session };
}
