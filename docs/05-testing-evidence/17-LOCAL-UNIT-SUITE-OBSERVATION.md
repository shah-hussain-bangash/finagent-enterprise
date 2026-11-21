# Local unit-suite observation

**Recorded:** 21 November 2026. **Revision tested:** `75afef9` on `fix/infra-validation`.
This is a partial, local result, not a passed release gate or a hosted CI result.

`python -m pytest tests/unit -q --tb=short -p no:cacheprovider` was run with Python 3.12,
the existing project dependencies and an isolated copy of the pinned WebAuthn dependencies.
A fresh, uniquely named test temporary directory was used. The result was **401 passed,
3 failed, 2 skipped and 23 subtests passed**.

The three failures were:

- `test_sandbox_isolates_parsing_with_time_and_memory_limits` in `test_documents.py`.
- Both cases of `test_real_parser_transport_returns_enforced_metadata` in
  `test_parser_network_isolation.py`. Both reported `PARSER_LINUX_ENGINE_UNAVAILABLE`.

The sandbox session could not access the Docker engine, and `wsl --status` failed due to its
logon session. An initial run also encountered a temporary directory permission error; the
fresh-directory rerun resolved that error.

## Follow-up in the user's terminal

On 21 November 2026, the user reported rerunning the two named test functions in their own
PowerShell session against `finagent-ready`: **3 passed in 4.54 seconds**, including both
parameterized parser transport cases. This is a user-reported focused result, not a complete
rerun of the unit suite or hosted CI. It shows that the three parser cases passed in that terminal;
the isolated session's runtime-access failure is not evidence of a parser defect there.

The skipped tests were an access-policy test requiring `FINAGENT_TEST_DATABASE_URL`, which was
not supplied, and a Linux-only seccomp runtime test. They were **not run**, not passed. No test
database was reset, no Azure service was contacted and no cloud resources were created. Raw
console output was not retained separately. Rerun the complete unit suite, including the skipped
checks in an appropriate environment, before treating the suite as fully verified.
