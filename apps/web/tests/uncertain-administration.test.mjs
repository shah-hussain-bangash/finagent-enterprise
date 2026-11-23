import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function page(path) {
  const mocks = {
    "next/link": { __esModule: true, default: ({ href, children }) => React.createElement("a", { href }, children) },
    "@/app/actions": { reconcile() {}, createDelegation() {}, revokeDelegation() {}, setFlag() {}, verifyAuthenticator() {}, sendEmailVerification() {}, confirmEmailVerification() {} },
    "@/components/SubmitButton": { SubmitButton: ({ children }) => React.createElement("button", null, children) },
    "@/components/OperationsDashboard": { OperationsDashboard: () => React.createElement("p", null, "Dashboard available") },
    "@/components/ui": { Empty: ({ children }) => React.createElement("p", null, children), ErrorState: () => null, Flash: () => null, When: () => null },
    "@/lib/session": { requireSession: async () => {} },
    "@/lib/auth-config": { mobileEntryEnabled: () => false },
    "@/lib/api": { load: async (url) => {
      if (url.startsWith("/api/v1/operations")) return { data: { items: [{ operation_id: "operation-1", operation_type: "SYNTHETIC", target_system: "mock", status: "OUTCOME_UNCERTAIN", attempts: 1 }] }, error: null };
      if (url.startsWith("/api/v1/reporting")) return { data: { can_reconcile: true }, error: null };
      if (url.startsWith("/api/v1/admin")) return { data: { items: [{ delegation_id: "delegation-1", delegator_id: "person-a", delegate_id: "person-b", role: "ANALYST", active: true, revoked_at: null }] }, error: null };
      if (url.includes("/email/")) return { data: { email_verified: false, challenge_id: "challenge", grants_mfa: false }, error: null };
      return { data: { enrolled: true, mfa_satisfied: false }, error: null };
    } },
  };
  const source = readFileSync(new URL(`../app/${path}/page.tsx`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const exports = {};
  new Function("require", "exports", "module", code)((id) => id in mocks ? mocks[id] : require(id), exports, { exports });
  return exports.default;
}

for (const [path, preserved, action, reload] of [
  ["operations", "SYNTHETIC", "Record reconciliation", "Reload operations"],
  ["admin", "Delegations", "Create delegation", "Reload administration"],
  ["security", "Current verification", "Verify authenticator", "Reload security verification"],
  ["security/email", "Email verification:", "Send verification email", "Reload email verification"],
]) {
  test(`${path} preserves status and requires a reload after an interrupted action`, async () => {
    const render = async (search) => renderToStaticMarkup(await page(path)({ searchParams: Promise.resolve(search) }));
    const normal = await render({});
    assert.ok(normal.includes(action));
    const uncertain = await render({ error: "CONNECTION_ERROR: Outcome unknown" });
    assert.ok(uncertain.includes(preserved));
    assert.ok(uncertain.includes(reload));
    assert.ok(!uncertain.includes(`>${action}</button>`));
    if (path === "admin") {
      assert.ok(!uncertain.includes(">Update flag</button>"));
      assert.ok(!uncertain.includes(">Revoke</button>"));
    }
    if (path === "security") assert.ok(!uncertain.includes("Verification failed"));
    if (path === "security/email") assert.ok(!uncertain.includes(">Verify email</button>"));
  });
}
