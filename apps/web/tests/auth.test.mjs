import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign, createHash } from "node:crypto";
import * as oidc from "openid-client";
import { authMode, entraSettings } from "./auth-config.js";
import { sealRecord, openRecord, isSession, newHandle, validHandle } from "./auth-store.js";
import { beginLogin, finishLogin, requireOrigin, sameValue } from "./entra.js";
import { NextRequest } from "next/server.js";
import { POST } from "./start-route.js";
import { GET } from "./callback-route.js";
import { authStore, FLOW_COOKIE, SESSION_COOKIE } from "./auth-store.js";

const tenant = "11111111-1111-1111-1111-111111111111";
const clientId = "22222222-2222-2222-2222-222222222222";
const subject = "33333333-3333-3333-3333-333333333333";
const env = {
  FINAGENT_ENVIRONMENT: "test", FINAGENT_WEB_AUTH_MODE: "entra",
  FINAGENT_ENTRA_TENANT_ID: tenant, FINAGENT_ENTRA_WEB_CLIENT_ID: clientId,
  FINAGENT_ENTRA_WEB_CLIENT_SECRET: "synthetic-client-secret",
  FINAGENT_ENTRA_API_SCOPE: `api://${tenant}/access_as_user`,
  FINAGENT_WEB_ORIGIN: "http://127.0.0.1:3001",
  FINAGENT_WEB_AUTH_REDIS_URL: "redis://127.0.0.1:56379/2",
};
const cfg = entraSettings(env);
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const forged = generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
const jwk = { ...publicKey.export({ format: "jwk" }), kid: "test-key", use: "sig", alg: "RS256" };

function memoryStore() {
  const records = new Map();
  return {
    records,
    async put(kind, id, value, ttl) { records.set(`${kind}:${id}`, { value: structuredClone(value), ttl }); },
    async read(kind, id, consume) {
      const key = `${kind}:${id}`, result = records.get(key)?.value ?? null;
      if (consume) records.delete(key);
      return result;
    },
    async remove(kind, id) { records.delete(`${kind}:${id}`); },
  };
}

async function fixture(options = {}) {
  const store = memoryStore();
  let flow, tokenCalls = 0, apiCalls = 0;
  const config = new oidc.Configuration({
    issuer: cfg.issuer,
    authorization_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize`,
    token_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`,
    jwks_uri: `https://login.microsoftonline.com/${tenant}/discovery/v2.0/keys`,
    id_token_signing_alg_values_supported: ["RS256"],
    response_types_supported: ["code"], subject_types_supported: ["public"],
  }, clientId, { id_token_signed_response_alg: "RS256" }, oidc.ClientSecretPost(cfg.clientSecret));
  oidc.enableNonRepudiationChecks(config);
  config[oidc.customFetch] = async (url, init) => {
    assert.equal(new URL(url).hostname, "login.microsoftonline.com");
    if (String(url).endsWith("/keys")) return Response.json({ keys: [jwk] });
    tokenCalls++;
    const params = new URLSearchParams(String(init.body));
    assert.equal(params.get("code_verifier"), flow.verifier);
    assert.equal(params.get("redirect_uri"), cfg.redirectUri);
    assert.equal(params.get("client_secret"), cfg.clientSecret);
    if (options.tokenError) return Response.json({ error: "invalid_grant" }, { status: 400 });
    const now = Math.floor(Date.now() / 1000);
    const claims = { iss: cfg.issuer, aud: clientId, sub: "pairwise-subject", oid: subject,
      tid: tenant, iat: now, exp: now + 1200, nonce: flow.nonce, ...options.claims };
    const unsigned = `${encode({ alg: "RS256", typ: "JWT", kid: "test-key" })}.${encode(claims)}`;
    const idToken = `${unsigned}.${sign("RSA-SHA256", Buffer.from(unsigned), options.forged ? forged : privateKey).toString("base64url")}`;
    return Response.json({ access_token: "synthetic-api-access-token", token_type: "Bearer", expires_in: 3600,
      id_token: idToken, refresh_token: "synthetic-never-store-this", ...options.tokens });
  };
  const login = await beginLogin(cfg, config, store, options.returnTo);
  flow = await store.read("flow", login.handle);
  const callback = new URL(cfg.redirectUri);
  callback.searchParams.set("code", "synthetic-code"); callback.searchParams.set("state", flow.state);
  const checkApi = async (token) => {
    apiCalls++; assert.equal(token, options.tokens?.access_token ?? "synthetic-api-access-token");
    if (options.apiError) throw new Error("Unknown or suspended identity");
    return { issuer: cfg.issuer, subject, display_name: "Synthetic user", ...options.identity };
  };
  return { store, config, login, flow, callback, checkApi, calls: () => ({ tokenCalls, apiCalls }) };
}

test("explicit Entra configuration uses one fixed tenant, origin and delegated scope", () => {
  assert.equal(authMode({}), "local");
  assert.equal(cfg.issuer, `https://login.microsoftonline.com/${tenant}/v2.0`);
  assert.equal(cfg.redirectUri, "http://127.0.0.1:3001/auth/entra/callback");
  assert.equal(cfg.secure, false);
  assert.throws(() => authMode({ FINAGENT_WEB_AUTH_MODE: "automatic" }));
  assert.throws(() => authMode({ FINAGENT_ENVIRONMENT: "production" }));
});

for (const [name, change] of [
  ["common authority", { FINAGENT_ENTRA_TENANT_ID: "common" }],
  ["missing secret", { FINAGENT_ENTRA_WEB_CLIENT_SECRET: "" }],
  ["external HTTP", { FINAGENT_WEB_ORIGIN: "http://example.com" }],
  ["origin with path", { FINAGENT_WEB_ORIGIN: "https://example.com/path" }],
  ["default scope", { FINAGENT_ENTRA_API_SCOPE: `api://${tenant}/.default` }],
  ["multiple scopes", { FINAGENT_ENTRA_API_SCOPE: `${env.FINAGENT_ENTRA_API_SCOPE} offline_access` }],
  ["unencrypted production Redis", { FINAGENT_ENVIRONMENT: "production", FINAGENT_WEB_ORIGIN: "https://example.com" }],
]) test(`configuration rejects ${name}`, () => assert.throws(() => entraSettings({ ...env, ...change })));

test("origin and handles reject forged input", () => {
  requireOrigin(cfg.origin, cfg);
  for (const origin of [null, "https://attacker.invalid", `${cfg.origin}.attacker.invalid`]) assert.throws(() => requireOrigin(origin, cfg));
  assert.ok(validHandle(newHandle()));
  assert.equal(validHandle("../session"), false);
  assert.equal(sameValue("é", "a"), false);
});

test("encrypted records are confidential, authenticated and bound to their storage key", () => {
  const sealed = sealRecord({ token: "synthetic-access-token" }, "session:one");
  assert.ok(!sealed.includes("synthetic-access-token"));
  assert.deepEqual(openRecord(sealed, "session:one"), { token: "synthetic-access-token" });
  assert.throws(() => openRecord(sealed, "session:two"));
  const bytes = Buffer.from(sealed, "base64url"); bytes[bytes.length - 1] ^= 1;
  assert.throws(() => openRecord(bytes.toString("base64url"), "session:one"));
});

test("case destination is bound to the Entra login transaction", async () => {
  const path = "/cases/bf69eaa7-86f5-428f-8e1d-bfa2c57420fd/evidence";
  const f = await fixture({ returnTo: path });
  assert.equal(f.flow.returnTo, path);
  assert.equal((await finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store)).returnTo, path);
  const invalid = await fixture({ returnTo: "//evil.example" });
  assert.equal(invalid.flow.returnTo, undefined);
  assert.equal((await finishLogin(cfg, invalid.config, invalid.callback, invalid.login.handle, invalid.checkApi, invalid.store)).returnTo, undefined);
});

test("code flow uses state, nonce, S256 and an exact callback, retaining no refresh or ID token", async () => {
  const f = await fixture(), url = new URL(f.login.location);
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("response_mode"), "query");
  assert.equal(url.searchParams.get("scope"), `openid profile ${cfg.scope}`);
  assert.equal(url.searchParams.get("nonce"), f.flow.nonce);
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.equal(url.searchParams.get("code_challenge"), createHash("sha256").update(f.flow.verifier).digest("base64url"));
  const result = await finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store);
  assert.ok(validHandle(result.handle)); assert.notEqual(result.handle, f.login.handle);
  assert.ok(isSession(result.session));
  assert.deepEqual(Object.keys(result.session).sort(), ["exp", "subject", "token"]);
  assert.ok(result.session.exp <= Math.floor(Date.now() / 1000) + 1200);
  assert.deepEqual(f.calls(), { tokenCalls: 1, apiCalls: 1 });
  await assert.rejects(() => finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store));
  assert.deepEqual(f.calls(), { tokenCalls: 1, apiCalls: 1 });
});

for (const [name, change] of [
  ["wrong nonce", { claims: { nonce: "wrong" } }],
  ["wrong issuer", { claims: { iss: "https://attacker.invalid" } }],
  ["wrong audience", { claims: { aud: "another-application" } }],
  ["wrong tenant", { claims: { tid: "other-tenant" } }],
  ["missing oid", { claims: { oid: null } }],
  ["expired ID token", { claims: { exp: 1 } }],
  ["forged signature", { forged: true }],
  ["token endpoint denial", { tokenError: true }],
  ["API identity mismatch", { identity: { subject: "another-user" } }],
  ["API issuer mismatch", { identity: { issuer: "another-issuer" } }],
  ["API resolver denial", { apiError: true }],
  ["expired access token response", { tokens: { expires_in: 0 } }],
]) test(`callback rejects ${name} without creating a session`, async () => {
  const f = await fixture(change);
  await assert.rejects(() => finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store));
  assert.equal([...f.store.records.keys()].filter((key) => key.startsWith("session:")).length, 0);
});

for (const name of ["wrong state", "duplicate state", "missing cookie", "expired transaction", "provider error"]) {
  test(`callback rejects ${name} before token exchange`, async () => {
    const f = await fixture();
    let handle = f.login.handle;
    if (name === "wrong state") f.callback.searchParams.set("state", "wrong");
    if (name === "duplicate state") f.callback.searchParams.append("state", f.flow.state);
    if (name === "missing cookie") handle = "";
    if (name === "expired transaction") f.flow.exp = 1;
    if (name === "provider error") f.callback.searchParams.set("error", "access_denied");
    await assert.rejects(() => finishLogin(cfg, f.config, f.callback, handle, f.checkApi, f.store));
    assert.equal(f.calls().tokenCalls, 0);
  });
}

test("session store failure cannot create a usable session", async () => {
  const f = await fixture();
  f.store.put = async () => { throw new Error("Store unavailable"); };
  await assert.rejects(() => finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store));
});

test("expired and malformed session records are rejected", () => {
  assert.equal(isSession({ token: "x", subject, exp: 1 }), false);
  assert.equal(isSession({ token: "x", subject, exp: Infinity }), false);
  assert.equal(isSession({ token: "", subject, exp: 9999999999 }), false);
  assert.equal(isSession(null), false);
});

test("start route rejects cross-origin and missing-origin requests before network activity", async () => {
  const before = { ...process.env }, oldFetch = globalThis.fetch;
  Object.assign(process.env, env);
  globalThis.fetch = async () => { throw new Error("Unexpected network request"); };
  try {
    for (const origin of [undefined, "https://attacker.invalid"]) {
      const response = await POST(new NextRequest(`${cfg.origin}/auth/entra/start`, {
        method: "POST", headers: origin ? { origin } : {},
      }));
      assert.equal(response.status, 400);
      assert.equal(response.headers.get("cache-control"), "no-store");
    }
  } finally { globalThis.fetch = oldFetch; process.env = before; }
});

test("route handlers complete a mocked login using opaque cookies and rotate the old session", async () => {
  const before = { ...process.env }, oldFetch = globalThis.fetch, methods = { ...authStore };
  const store = memoryStore();
  Object.assign(process.env, env);
  Object.assign(authStore, store);
  let flow;
  globalThis.fetch = async (url, init) => {
    const target = String(url);
    if (target.endsWith("/auth/config")) return Response.json({ mode: "entra" });
    if (target.endsWith("/.well-known/openid-configuration")) return Response.json({
      issuer: cfg.issuer,
      authorization_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize`,
      token_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`,
      jwks_uri: `https://login.microsoftonline.com/${tenant}/discovery/v2.0/keys`,
      id_token_signing_alg_values_supported: ["RS256"], response_types_supported: ["code"],
      subject_types_supported: ["public"], token_endpoint_auth_methods_supported: ["client_secret_post"],
    });
    if (target.endsWith("/keys")) return Response.json({ keys: [jwk] });
    if (target.endsWith("/token")) {
      const params = new URLSearchParams(String(init.body));
      assert.equal(params.get("code_verifier"), flow.verifier);
      const now = Math.floor(Date.now() / 1000);
      const unsigned = `${encode({ alg: "RS256", kid: "test-key" })}.${encode({
        iss: cfg.issuer, aud: clientId, sub: "synthetic", oid: subject, tid: tenant,
        exp: now + 600, iat: now, nonce: flow.nonce,
      })}`;
      return Response.json({ access_token: "synthetic-api-access-token", token_type: "Bearer", expires_in: 600,
        id_token: `${unsigned}.${sign("RSA-SHA256", Buffer.from(unsigned), privateKey).toString("base64url")}` });
    }
    if (target.endsWith("/auth/session")) {
      assert.equal(init.headers.Authorization, "Bearer synthetic-api-access-token");
      return Response.json({ issuer: cfg.issuer, subject, display_name: "Synthetic user" });
    }
    throw new Error("Unexpected network target");
  };
  try {
    const casePath = "/cases/bf69eaa7-86f5-428f-8e1d-bfa2c57420fd/evidence";
    const start = await POST(new NextRequest(`${cfg.origin}/auth/entra/start?returnTo=${encodeURIComponent(casePath)}`, { method: "POST", headers: { origin: cfg.origin } }));
    assert.equal(start.status, 303);
    const flowCookie = start.cookies.get(FLOW_COOKIE);
    assert.ok(validHandle(flowCookie.value));
    assert.match(start.headers.get("set-cookie"), /HttpOnly/);
    assert.match(start.headers.get("set-cookie"), /SameSite=lax/i);
    assert.match(start.headers.get("set-cookie"), /Path=\//);
    flow = await store.read("flow", flowCookie.value);
    const old = newHandle();
    await store.put("session", old, { token: "old", subject, exp: flow.exp }, 300);
    const callback = new URL(cfg.redirectUri);
    callback.search = new URLSearchParams({ code: "synthetic-code", state: flow.state, returnTo: "https://attacker.invalid" }).toString();
    const response = await GET(new NextRequest(callback, {
      headers: { cookie: `${FLOW_COOKIE}=${flowCookie.value}; ${SESSION_COOKIE}=${old}` },
    }));
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), `${cfg.origin}${casePath}`);
    assert.equal(response.headers.get("referrer-policy"), "no-referrer");
    assert.equal(response.headers.get("cache-control"), "no-store");
    const sessionCookie = response.cookies.get(SESSION_COOKIE);
    assert.ok(validHandle(sessionCookie.value));
    assert.equal(await store.read("session", old), null);
    assert.ok(isSession(await store.read("session", sessionCookie.value)));
    assert.ok(!response.headers.get("set-cookie").includes("synthetic-api-access-token"));
    assert.equal(response.cookies.get(FLOW_COOKIE).value, "");
    const replay = await GET(new NextRequest(callback, { headers: { cookie: `${FLOW_COOKIE}=${flowCookie.value}` } }));
    assert.ok(replay.headers.get("location").startsWith(`${cfg.origin}/signin?error=`));
    assert.equal(replay.cookies.get(SESSION_COOKIE), undefined);
  } finally {
    Object.assign(authStore, methods);
    globalThis.fetch = oldFetch;
    process.env = before;
  }
});
