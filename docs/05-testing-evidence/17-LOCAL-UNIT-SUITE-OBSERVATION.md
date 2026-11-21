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
logon session. These observations limit diagnosis; they do not establish whether the parser
tests will pass in the user's own terminal or in CI. An initial run also encountered a temporary
directory permission error; the fresh-directory rerun resolved that error.

The skipped tests were an access-policy test requiring `FINAGENT_TEST_DATABASE_URL`, which was
not supplied, and a Linux-only seccomp runtime test. They were **not run**, not passed. No test
database was reset, no Azure service was contacted and no cloud resources were created. Raw
console output was not retained separately. Rerun the parser tests in an environment with the
required Linux runtime and investigate any remaining failures before treating this suite as green.
