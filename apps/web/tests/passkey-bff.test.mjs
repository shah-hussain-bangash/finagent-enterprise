import { test } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server.js";
import { POST } from "./route.js";

const origin = "https://phone.example.ts.net";
function request(body, headers = {}) {
  return new NextRequest(origin + "/security/passkeys/api", { method: "POST",
    headers: { Origin: origin, "Content-Type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body) });
}

function setup() {
  globalThis.mobileEnabled = true;
  globalThis.testSession = { token: "fa_pwd_synthetic-test-only", subject: "synthetic-reviewer", exp: 9999999999 };
  let calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json({ registered: true, grants_mfa: false }); };
  return calls;
}

test("desktop entry denies passkey proxy", async () => {
  const calls = setup(); globalThis.mobileEnabled = false;
  assert.equal((await POST(request({ operation: "verify-options" }))).status, 404);
  assert.equal(calls.length, 0);
});

for (const [label, body, headers, expected] of [
  ["wrong origin", { operation: "verify-options" }, { Origin: "https://untrusted.invalid" }, 400],
  ["missing origin", { operation: "verify-options" }, { Origin: "" }, 400],
  ["wrong content type", { operation: "verify-options" }, { "Content-Type": "text/plain" }, 400],
  ["unknown operation", { operation: "change-role" }, {}, 400],
  ["inherited operation name", { operation: "__proto__" }, {}, 400],
  ["client authority", { operation: "verify-options", mfa: true }, {}, 400],
  ["malformed JSON", "{", {}, 400],
  ["body array", [], {}, 400],
  ["oversized body", "x".repeat(32769), {}, 413],
]) test(`BFF rejects ${label} before API access`, async () => {
  const calls = setup(); assert.equal((await POST(request(body, headers))).status, expected);
  assert.equal(calls.length, 0);
});

for (const session of [null, { token: "provider-token" }]) test("password session required: " + String(session?.token), async () => {
  const calls = setup(); globalThis.testSession = session;
  assert.equal((await POST(request({ operation: "verify-options" }))).status, 401);
  assert.equal(calls.length, 0);
});

for (const [operation, suffix] of [["register-options", "/register/options"], ["register-finish", "/register/finish"],
  ["verify-options", "/verify/options"], ["verify-finish", "/verify/finish"], ["revoke", "/revoke"]]) {
  test("BFF forwards only the authenticated fixed operation: " + operation, async () => {
    const calls = setup();
    const response = await POST(request({ operation, payload: { challenge_id: "synthetic" } }));
    assert.equal(response.status, 200);
    assert.equal(calls[0].url, "http://api:8000/api/v1/auth/passkeys" + suffix);
    assert.equal(calls[0].options.headers.Authorization, "Bearer fa_pwd_synthetic-test-only");
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(JSON.stringify(await response.json()).includes("fa_pwd_"), false);
  });
}

test("downstream denial preserves status and excludes sensitive error content", async () => {
  setup(); globalThis.fetch = async () => new Response("private backend details", { status: 403 });
  const response = await POST(request({ operation: "verify-finish" }));
  assert.equal(response.status, 403);
  assert.equal((await response.text()).includes("private backend details"), false);
});
