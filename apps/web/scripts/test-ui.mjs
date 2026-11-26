import { readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

// These suites require their own temporary compiled modules and dedicated scripts.
const dedicated = new Set(["auth.test.mjs", "google-auth.test.mjs", "password-auth.test.mjs", "passkey-bff.test.mjs"]);
const tests = readdirSync("tests")
  .filter((name) => name.endsWith(".test.mjs") && !dedicated.has(name))
  .sort()
  .map((name) => join("tests", name));

if (tests.length === 0) throw new Error("No UI tests found");
const result = spawnSync(process.execPath, ["--test", ...tests], { stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
