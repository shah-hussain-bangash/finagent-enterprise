// Exercise the actual BFF route with synthetic session and downstream API boundaries.
import { mkdtemp, readFile, writeFile, rm, symlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import ts from "typescript";

const root = resolve(".");
const output = await mkdtemp(join(tmpdir(), "finagent-passkey-bff-"));
try {
  await writeFile(join(output, "package.json"), '{"type":"module"}');
  await symlink(join(root, "node_modules"), join(output, "node_modules"), process.platform === "win32" ? "junction" : "dir");
  const source = await readFile(join(root, "app/security/passkeys/api/route.ts"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText
    .replace('import "server-only";', '').replace('from "next/server"', 'from "next/server.js"')
    .replace('from "@/lib/auth-config"', 'from "./auth-config.js"')
    .replace('from "@/lib/api"', 'from "./api.js"').replace('from "@/lib/session"', 'from "./session.js"');
  await writeFile(join(output, "route.js"), code);
  await writeFile(join(output, "auth-config.js"), 'export const mobileEntryEnabled=()=>globalThis.mobileEnabled; export const externalSettings=()=>({origin:"https://phone.example.ts.net"});');
  await writeFile(join(output, "api.js"), 'export const BASE="http://api:8000";');
  await writeFile(join(output, "session.js"), 'export const getSession=async()=>globalThis.testSession;');
  await writeFile(join(output, "passkey-bff.test.mjs"), await readFile(join(root, "tests/passkey-bff.test.mjs")));
  const result = spawnSync(process.execPath, ["--test", join(output, "passkey-bff.test.mjs")], { stdio: "inherit" });
  process.exitCode = result.status ?? 1;
} finally { await rm(output, { recursive: true, force: true }); }
