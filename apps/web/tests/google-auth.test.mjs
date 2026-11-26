import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign, createHash } from "node:crypto";
import * as oidc from "openid-client";
import { authMode, googleSettings } from "./auth-config.js";
import { sealRecord, openRecord, isSession, newHandle, validHandle } from "./auth-store.js";
import { beginLogin, finishLogin, requireOrigin, sameValue, googleConfiguration } from "./google.js";
import { NextRequest } from "next/server.js";
import { POST } from "./google-start-route.js";
import { GET } from "./google-callback-route.js";
import { authStore, flowCookieName, sessionCookieName } from "./auth-store.js";
const FLOW_COOKIE = flowCookieName(false, "google"), SESSION_COOKIE = sessionCookieName(false, "google");

const clientId = "123456789-synthetic.apps.googleusercontent.com";
const subject = "33333333-3333-3333-3333-333333333333";
const env = {
  FINAGENT_ENVIRONMENT: "test", FINAGENT_WEB_AUTH_MODE: "google",
  FINAGENT_GOOGLE_WEB_CLIENT_ID: clientId,
  FINAGENT_GOOGLE_WEB_CLIENT_SECRET: "synthetic-client-secret",
  FINAGENT_WEB_ORIGIN: "http://127.0.0.1:3001",
  FINAGENT_WEB_AUTH_REDIS_URL: "redis://127.0.0.1:56379/2",
};
const cfg = googleSettings(env);
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
    authorization_endpoint: "https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint: "https://oauth2.googleapis.com/token",
    jwks_uri: "https://www.googleapis.com/oauth2/v3/certs",
    id_token_signing_alg_values_supported: ["RS256"],
    response_types_supported: ["code"], subject_types_supported: ["public"],
  }, clientId, { id_token_signed_response_alg: "RS256" }, oidc.ClientSecretPost(cfg.clientSecret));
  oidc.enableNonRepudiationChecks(config);
  config[oidc.customFetch] = async (url, init) => {
    assert.ok(["oauth2.googleapis.com", "www.googleapis.com"].includes(new URL(url).hostname));
    if (String(url).endsWith("/certs")) return Response.json({ keys: [jwk] });
    tokenCalls++;
    const params = new URLSearchParams(String(init.body));
    assert.equal(params.get("code_verifier"), flow.verifier);
    assert.equal(params.get("redirect_uri"), cfg.redirectUri);
    assert.equal(params.get("client_secret"), cfg.clientSecret);
    if (options.tokenError) return Response.json({ error: "invalid_grant" }, { status: 400 });
    const now = Math.floor(Date.now() / 1000);
    const claims = { iss: cfg.issuer, aud: clientId, sub: subject,
      iat: now, exp: now + 1200, nonce: flow.nonce, ...options.claims };
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
    apiCalls++; assert.equal(JSON.parse(Buffer.from(token.split(".")[1], "base64url")).sub, subject);
    if (options.apiError) throw new Error("Unknown or suspended identity");
    return { issuer: cfg.issuer, subject, display_name: "Synthetic user", ...options.identity };
  };
  return { store, config, login, flow, callback, checkApi, calls: () => ({ tokenCalls, apiCalls }) };
}

test("explicit Google configuration uses one fixed issuer, origin and client audience", () => {
  assert.equal(authMode({}), "local");
  assert.equal(cfg.issuer, "https://accounts.google.com");
  assert.equal(cfg.redirectUri, "http://127.0.0.1:3001/auth/google/callback");
  assert.equal(cfg.secure, false);
  assert.throws(() => authMode({ FINAGENT_WEB_AUTH_MODE: "automatic" }));
  assert.throws(() => authMode({ FINAGENT_ENVIRONMENT: "production" }));
});

for (const [name, change] of [
  ["missing client ID", { FINAGENT_GOOGLE_WEB_CLIENT_ID: "" }],
  ["wrong client format", { FINAGENT_GOOGLE_WEB_CLIENT_ID: "another-provider" }],
  ["missing secret", { FINAGENT_GOOGLE_WEB_CLIENT_SECRET: "" }],
  ["external HTTP", { FINAGENT_WEB_ORIGIN: "http://example.com" }],
  ["origin with path", { FINAGENT_WEB_ORIGIN: "https://example.com/path" }],
  ["unencrypted production Redis", { FINAGENT_ENVIRONMENT: "production", FINAGENT_WEB_ORIGIN: "https://example.com" }],
]) test(`configuration rejects ${name}`, () => assert.throws(() => googleSettings({ ...env, ...change })));

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

test("case destination is bound to the Google login transaction", async () => {
  const path = "/cases/bf69eaa7-86f5-428f-8e1d-bfa2c57420fd/report";
  const f = await fixture({ returnTo: path });
  assert.equal(f.flow.returnTo, path);
  assert.equal((await finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store)).returnTo, path);
  const invalid = await fixture({ returnTo: "//evil.example" });
  assert.equal(invalid.flow.returnTo, undefined);
  assert.equal((await finishLogin(cfg, invalid.config, invalid.callback, invalid.login.handle, invalid.checkApi, invalid.store)).returnTo, undefined);
});

test("code flow uses state, nonce, S256 and an exact callback, retaining only the encrypted ID credential", async () => {
  const f = await fixture(), url = new URL(f.login.location);
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("response_mode"), "query");
  assert.equal(url.searchParams.get("scope"), "openid email profile");
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
  ["wrong authorised party", { claims: { azp: "other-client" } }],
  ["missing subject", { claims: { sub: null } }],
  ["expired ID token", { claims: { exp: 1 } }],
  ["forged signature", { forged: true }],
  ["token endpoint denial", { tokenError: true }],
  ["API identity mismatch", { identity: { subject: "another-user" } }],
  ["API issuer mismatch", { identity: { issuer: "another-issuer" } }],
  ["API resolver denial", { apiError: true }],
  ["missing ID credential", { tokens: { id_token: undefined } }],
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
      const response = await POST(new NextRequest(`${cfg.origin}/auth/google/start`, {
        method: "POST", headers: origin ? { origin } : {},
      }));
      assert.equal(response.status, 400);
      assert.equal(response.headers.get("cache-control"), "no-store");
    }
  } finally { globalThis.fetch = oldFetch; process.env = before; }
});

for (const endpoint of ["authorization_endpoint", "token_endpoint", "jwks_uri"]) {
  test(`Google discovery rejects untrusted ${endpoint}`, async () => {
    const oldFetch = globalThis.fetch;
    globalThis.fetch = async () => Response.json({
      issuer: cfg.issuer,
      authorization_endpoint: "https://accounts.google.com/o/oauth2/v2/auth",
      token_endpoint: "https://oauth2.googleapis.com/token",
      jwks_uri: "https://www.googleapis.com/oauth2/v3/certs",
      [endpoint]: "https://attacker.invalid/credentials",
    });
    try { await assert.rejects(() => googleConfiguration(cfg)); }
    finally { globalThis.fetch = oldFetch; }
  });
}

test("route handlers complete a mocked login using opaque cookies and rotate the old session", async () => {
  const before = { ...process.env }, oldFetch = globalThis.fetch, methods = { ...authStore };
  const store = memoryStore();
  Object.assign(process.env, env);
  Object.assign(authStore, store);
  let flow;
  globalThis.fetch = async (url, init) => {
    const target = String(url);
    if (target.endsWith("/auth/config")) return Response.json({ mode: "google" });
    if (target.endsWith("/.well-known/openid-configuration")) return Response.json({
      issuer: cfg.issuer,
      authorization_endpoint: "https://accounts.google.com/o/oauth2/v2/auth",
      token_endpoint: "https://oauth2.googleapis.com/token",
      jwks_uri: "https://www.googleapis.com/oauth2/v3/certs",
      id_token_signing_alg_values_supported: ["RS256"], response_types_supported: ["code"],
      subject_types_supported: ["public"], token_endpoint_auth_methods_supported: ["client_secret_post"],
    });
    if (target.endsWith("/certs")) return Response.json({ keys: [jwk] });
    if (target.endsWith("/token")) {
      const params = new URLSearchParams(String(init.body));
      assert.equal(params.get("code_verifier"), flow.verifier);
      const now = Math.floor(Date.now() / 1000);
      const unsigned = `${encode({ alg: "RS256", kid: "test-key" })}.${encode({
        iss: cfg.issuer, aud: clientId, sub: subject,
        exp: now + 600, iat: now, nonce: flow.nonce,
      })}`;
      return Response.json({ access_token: "synthetic-api-access-token", token_type: "Bearer", expires_in: 600,
        id_token: `${unsigned}.${sign("RSA-SHA256", Buffer.from(unsigned), privateKey).toString("base64url")}` });
    }
    if (target.endsWith("/auth/session")) {
      assert.ok(init.headers.Authorization.startsWith("Bearer ey"));
      return Response.json({ issuer: cfg.issuer, subject, display_name: "Synthetic user" });
    }
    throw new Error("Unexpected network target");
  };
  try {
    const casePath = "/cases/bf69eaa7-86f5-428f-8e1d-bfa2c57420fd/evidence";
    const start = await POST(new NextRequest(`${cfg.origin}/auth/google/start?returnTo=${encodeURIComponent(casePath)}`, { method: "POST", headers: { origin: cfg.origin } }));
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

test("provider cookies are separate and use host prefixes on HTTPS", () => {
  assert.notEqual(flowCookieName(false, "google"), flowCookieName(false));
  assert.notEqual(sessionCookieName(false, "google"), sessionCookieName(false));
  assert.equal(sessionCookieName(true, "google"), "__Host-fa_google_session");
});

test("Google API access and refresh tokens are discarded", async () => {
  const f = await fixture({ tokens: { expires_in: 0 } });
  const result = await finishLogin(cfg, f.config, f.callback, f.login.handle, f.checkApi, f.store);
  const stored = JSON.stringify([...f.store.records.values()]);
  assert.ok(!stored.includes("synthetic-api-access-token"));
  assert.ok(!stored.includes("synthetic-never-store-this"));
  assert.ok(result.session.token.startsWith("ey"));
});

test("Google start rejects API mode disagreement before discovery", async () => {
  const before = { ...process.env }, oldFetch = globalThis.fetch;
  Object.assign(process.env, env);
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ mode: "local" }); };
  try {
    const response = await POST(new NextRequest(`${cfg.origin}/auth/google/start`, {
      method: "POST", headers: { origin: cfg.origin },
    }));
    assert.equal(response.status, 303);
    assert.ok(response.headers.get("location").startsWith(`${cfg.origin}/signin?error=`));
    assert.equal(response.cookies.get(FLOW_COOKIE), undefined);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = oldFetch; process.env = before; }
});


test("mobile entry validates its fixed HTTPS configuration", async () => {
  const { mobileEntryEnabled } = await import("./auth-config.js");
  const good = { ...env, FINAGENT_WEB_MOBILE_ENTRY: "true",
    FINAGENT_WEB_PASSWORD_LOGIN_ENABLED: "true",
    FINAGENT_WEB_ORIGIN: "https://desktop-48ai2l6.tail9482f2.ts.net" };
  assert.equal(mobileEntryEnabled({}), false);
  assert.equal(mobileEntryEnabled(good), true);
  for (const change of [
    { FINAGENT_ENVIRONMENT: "production" }, { FINAGENT_WEB_AUTH_MODE: "local" },
    { FINAGENT_WEB_ORIGIN: "http://127.0.0.1:3002" },
    { FINAGENT_WEB_ORIGIN: "https://example.com" },
    { FINAGENT_WEB_ORIGIN: "https://desktop-48ai2l6.tail9482f2.ts.net/path" },
    { FINAGENT_WEB_PASSWORD_LOGIN_ENABLED: "false" }, { FINAGENT_WEB_MOBILE_ENTRY: "yes" },
  ]) assert.throws(() => mobileEntryEnabled({ ...good, ...change }));
});

test("mobile entry denies all primary-provider start and callback handlers before network access", async () => {
  const before = { ...process.env };
  const originalFetch = globalThis.fetch;
  let fetches = 0;
  try {
    Object.assign(process.env, env, { FINAGENT_WEB_MOBILE_ENTRY: "true",
      FINAGENT_WEB_PASSWORD_LOGIN_ENABLED: "true",
      FINAGENT_WEB_ORIGIN: "https://desktop-48ai2l6.tail9482f2.ts.net" });
    globalThis.fetch = async () => { fetches++; throw new Error("unexpected network"); };
    for (const [file, method] of [["google-start-route", "POST"], ["google-callback-route", "GET"],
      ["start-route", "POST"], ["callback-route", "GET"]]) {
      const route = await import(`./${file}.js`);
      const response = await route[method](new NextRequest("https://desktop-48ai2l6.tail9482f2.ts.net/auth/test", { method }));
      assert.equal(response.status, 404);
      assert.equal(response.headers.get("set-cookie"), null);
      assert.equal(response.headers.get("location"), null);
    }
    assert.equal(fetches, 0);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in before)) delete process.env[key];
    Object.assign(process.env, before);
  }
});
