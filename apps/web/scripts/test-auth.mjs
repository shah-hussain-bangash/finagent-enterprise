// Compile only the server authentication core into an isolated ESM test directory.
import { mkdtemp, readFile, writeFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const root = resolve(".");
const output = await mkdtemp(join(tmpdir(), "finagent-auth-"));
try {
  await writeFile(join(output, "package.json"), '{"type":"module"}');
  // Junctions work without Windows developer mode; dependencies remain in the project.
  await symlink(join(root, "node_modules"), join(output, "node_modules"), process.platform === "win32" ? "junction" : "dir");
  for (const name of ["auth-config", "auth-diagnostics", "auth-store", "entra", "google", "password-auth"]) {
    const source = await readFile(join(root, "lib", `${name}.ts`), "utf8");
    const result = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
    });
    const code = result.outputText.replace('import "server-only";', '').replace(/from "\.\/(auth-config|auth-diagnostics|auth-store|password-auth)"/g, 'from "./$1.js"');
    await writeFile(join(output, `${name}.js`), code);
  }
  for (const provider of ["entra", "google"]) {
  for (const name of ["start", "callback"]) {
    const source = await readFile(join(root, "app", "auth", provider, name, "route.ts"), "utf8");
    const result = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
    });
    const code = result.outputText
      .replace('import "server-only";', '')
      .replace('from "next/server"', 'from "next/server.js"')
      .replace(/from "@\/lib\/(auth-config|auth-store|entra|google|password-auth)"/g, 'from "./$1.js"');
    await writeFile(join(output, `${provider === "google" ? "google-" : ""}${name}-route.js`), code);
  }
  }
  {
    const source = await readFile(join(root, "app/auth/password/route.ts"), "utf8");
    const result = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
    });
    const code = result.outputText.replace('import "server-only";', '')
      .replace('from "next/server"', 'from "next/server.js"')
      .replace(/from "@\/lib\/(auth-config|auth-store|password-auth)"/g, 'from "./$1.js"');
    await writeFile(join(output, "password-route.js"), code);
  }
  if (process.argv.includes("--redis")) {
    const redisUrl = process.env.FINAGENT_WEB_AUTH_REDIS_URL;
    if (!redisUrl) throw new Error("Set FINAGENT_WEB_AUTH_REDIS_URL to the local test Redis URL first");
    const { randomUUID } = await import("node:crypto");
    Object.assign(process.env, {
      FINAGENT_ENVIRONMENT: "test", FINAGENT_WEB_AUTH_MODE: "entra",
      FINAGENT_ENTRA_TENANT_ID: randomUUID(), FINAGENT_ENTRA_WEB_CLIENT_ID: randomUUID(),
      FINAGENT_ENTRA_WEB_CLIENT_SECRET: "synthetic-store-test-only",
      FINAGENT_ENTRA_API_SCOPE: `api://${randomUUID()}/access_as_user`,
      FINAGENT_WEB_ORIGIN: "http://127.0.0.1:3001", FINAGENT_API_URL: "http://127.0.0.1:8001",
      FINAGENT_WEB_SESSION_SECRET: "synthetic-test-session-secret-0123456789012345",
    });
    const { authStore, newHandle, closeAuthStore } = await import(pathToFileURL(join(output, "auth-store.js")).href);
    const flowId = newHandle(), sessionId = newHandle();
    const assert = (await import("node:assert/strict")).default;
    try {
      const flow = { state: "synthetic", nonce: "synthetic", verifier: "synthetic", exp: Math.floor(Date.now() / 1000) + 60 };
      await authStore.put("flow", flowId, flow, 60);
      assert.deepEqual(await authStore.read("flow", flowId, true), flow);
      assert.equal(await authStore.read("flow", flowId, true), null);
      const session = { token: "synthetic-not-a-token", subject: "synthetic", exp: flow.exp };
      await authStore.put("session", sessionId, session, 60);
      await assert.rejects(() => authStore.put("session", sessionId, session, 60));
      assert.deepEqual(await authStore.read("session", sessionId), session);
      // Same handle in Google cannot read or overwrite an Entra record.
      Object.assign(process.env, { FINAGENT_WEB_AUTH_MODE: "google",
        FINAGENT_GOOGLE_WEB_CLIENT_ID: `${randomUUID()}.apps.googleusercontent.com`,
        FINAGENT_GOOGLE_WEB_CLIENT_SECRET: "synthetic-google-store-only" });
      try {
        assert.equal(await authStore.read("session", sessionId), null);
        await authStore.put("session", sessionId, { ...session, subject: "google-synthetic" }, 60);
        assert.equal((await authStore.read("session", sessionId)).subject, "google-synthetic");
        await authStore.remove("session", sessionId);
      } finally { process.env.FINAGENT_WEB_AUTH_MODE = "entra"; }
      assert.deepEqual(await authStore.read("session", sessionId), session);
      await authStore.remove("session", sessionId);
      assert.equal(await authStore.read("session", sessionId), null);
      console.log("PASS: local Redis encrypted record round-trip, one-use flow, collision rejection and session revocation and Google/Entra namespace isolation.");
      console.log("Only random, short-lived synthetic keys were used. No Entra, API or business request was made.");
    } finally {
      try { await authStore.remove("flow", flowId); await authStore.remove("session", sessionId); }
      finally { await closeAuthStore(); }
    }
  } else {
    await writeFile(join(output, "auth.test.mjs"), await readFile(join(root, "tests", "auth.test.mjs")));
    await writeFile(join(output, "google-auth.test.mjs"), await readFile(join(root, "tests", "google-auth.test.mjs")));
    await writeFile(join(output, "password-auth.test.mjs"), await readFile(join(root, "tests", "password-auth.test.mjs")));
    const result = spawnSync(process.execPath, ["--test", join(output, "auth.test.mjs"), join(output, "google-auth.test.mjs"), join(output, "password-auth.test.mjs")], {
      stdio: "inherit", env: { ...process.env, FINAGENT_WEB_SESSION_SECRET: "synthetic-test-session-secret-0123456789012345" },
    });
    process.exitCode = result.status ?? 1;
  }
} finally {
  await rm(output, { recursive: true, force: true });
}
