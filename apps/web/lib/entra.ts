import "server-only";
import * as oidc from "openid-client";
import { timingSafeEqual } from "node:crypto";
import type { EntraSettings } from "./auth-config";
import { authStore, newHandle, validHandle, type AuthStore, type LoginFlow, type StoredSession } from "./auth-store";

let configuration: Promise<oidc.Configuration> | undefined;
export async function entraConfiguration(cfg: EntraSettings): Promise<oidc.Configuration> {
  if (!configuration) {
    configuration = oidc.discovery(new URL(cfg.issuer), cfg.clientId, {
      id_token_signed_response_alg: "RS256",
    }, oidc.ClientSecretPost(cfg.clientSecret), {
      timeout: 10,
      execute: [oidc.enableNonRepudiationChecks],
    }).then((config) => {
      const metadata = config.serverMetadata();
      // Do not send credentials to endpoints on any other host, even via metadata.
      for (const endpoint of [metadata.authorization_endpoint, metadata.token_endpoint, metadata.jwks_uri]) {
        const url = new URL(endpoint ?? "");
        if (url.origin !== "https://login.microsoftonline.com" || url.username || url.password || url.hash) {
          throw new Error("Unexpected Entra endpoint");
        }
      }
      if (metadata.issuer !== cfg.issuer) throw new Error("Unexpected Entra issuer");
      return config;
    }).catch(() => {
      configuration = undefined;
      throw new Error("Entra discovery failed");
    });
  }
  return configuration;
}

export function sameValue(a: string, b: string): boolean {
  const left = Buffer.from(a), right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function requireOrigin(origin: string | null, cfg: EntraSettings): void {
  if (origin !== cfg.origin) throw new Error("Invalid sign-in origin");
}

export async function beginLogin(
  cfg: EntraSettings, config: oidc.Configuration, store: AuthStore = authStore,
): Promise<{ location: string; handle: string }> {
  const handle = newHandle();
  const flow: LoginFlow = {
    state: oidc.randomState(), nonce: oidc.randomNonce(), verifier: oidc.randomPKCECodeVerifier(),
    exp: Math.floor(Date.now() / 1000) + 300,
  };
  const location = oidc.buildAuthorizationUrl(config, {
    redirect_uri: cfg.redirectUri,
    scope: `openid profile ${cfg.scope}`,
    response_type: "code", response_mode: "query",
    code_challenge_method: "S256", code_challenge: await oidc.calculatePKCECodeChallenge(flow.verifier),
    state: flow.state, nonce: flow.nonce,
  }).href;
  await store.put("flow", handle, flow, 300);
  return { location, handle };
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
  cfg: EntraSettings, config: oidc.Configuration, requestUrl: URL, handle: string,
  checkApi: CheckApi = checkApiIdentity, store: AuthStore = authStore,
): Promise<{ handle: string; session: StoredSession }> {
  if (!validHandle(handle) || requestUrl.pathname !== "/auth/entra/callback" || requestUrl.search.length > 16384) {
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
  if (!claims || claims.iss !== cfg.issuer || claims.tid !== cfg.tenantId || typeof claims.oid !== "string"
      || !claims.oid || typeof claims.exp !== "number" || !Number.isFinite(claims.exp)
      || tokens.token_type.toLowerCase() !== "bearer" || !tokens.access_token || tokens.access_token.length > 24576
      || typeof tokens.expires_in !== "number" || !Number.isFinite(tokens.expires_in) || tokens.expires_in <= 0) {
    throw new Error("Invalid authentication response");
  }
  // API validates its own access token and resolves an ACTIVE human principal in its trusted database.
  const identity = await checkApi(tokens.access_token);
  if (identity.subject !== claims.oid || identity.issuer !== cfg.issuer) throw new Error("Identity mismatch");
  const exp = Math.floor(Math.min(Date.now() / 1000 + tokens.expires_in, claims.exp, Date.now() / 1000 + 3600));
  const ttl = exp - Math.floor(Date.now() / 1000);
  if (ttl < 1) throw new Error("Expired authentication response");
  const session: StoredSession = { token: tokens.access_token, subject: identity.subject, exp };
  const sessionHandle = newHandle();
  await store.put("session", sessionHandle, session, ttl);
  // ID and refresh tokens are not persisted. Reauthentication is required at expiry.
  return { handle: sessionHandle, session };
}
