# Parser network and process isolation

Status: **Verified in local development on 2026-10-21.**

Windows-host parsing through the dedicated Docker Linux image passed the synthetic isolation check. The rebuilt FinAgent worker also parsed synthetic CSV with network, memory and process enforcement flags true.

Project lint, type checking, unit tests, backend regression and browser regression passed within the scope recorded below. Production validation and requirement approval remain pending.

## Execution boundary

Untrusted PDF, DOCX and CSV bytes are passed to a dedicated parser child. The API and business worker retain their own network access. No host-wide firewall rule or Python socket monkeypatch is installed.

- Linux: a fresh `python -I -B` child closes inherited handles, receives a minimal environment, installs libseccomp restrictions and requires a successful RLIMIT_AS memory limit.
- Windows host: parsing runs in a one-shot Linux container in local Docker Desktop. Native Windows document parsing is disabled. The existing Windows Job Object helper remains for its memory-limit unit test; it is not a network-isolation mechanism.
- Other host platforms: parsing is refused with `PARSER_PLATFORM_UNSUPPORTED`.

The child denies socket creation and network calls, io_uring, process creation/replacement, ptrace, selected cross-process descriptor/memory modification and namespace operations.

The filter sets `no_new_privs` and checks native libc socket calls for IPv4 TCP/UDP, IPv6 TCP/UDP and Unix sockets. A successful denial must return EPERM; an unrelated socket error is not proof.

Third-party parsers are imported and document parsing begins only after network/process controls and memory limiting succeed. Limits apply to the child. The parent requires all enforcement metadata for any non-CRASHED result.

Seccomp does not provide a complete filesystem sandbox. In the Linux host/worker path, the child can still access files permitted to the service account. This change does not add a separate user, filesystem namespace, kernel exploit protection or universal syscall allowlist. Document resource limits and parser content checks remain necessary.

## Windows host prerequisites

Docker Desktop must run its Linux engine and the Docker CLI must be on PATH.

The transport uses only the local Windows named pipe:

    npipe:////./pipe/docker_engine

It does not select remote Docker contexts or DOCKER_HOST destinations. A temporary empty Docker client configuration avoids user-configured credential helpers and proxies. No image is auto-pulled.

Build the dedicated trusted image from the current checkout before running Windows-host parsing or tests:

```powershell
Set-Location -LiteralPath "C:\finagent-enterprise"

docker build -t finagent-parser:isolated-v1 -f apps/parser/Dockerfile .
```

The image contains Python, the two parser dependencies constrained by `requirements.lock`, the parser modules and libseccomp2. It contains no application `.env`, source document, database credential or Docker socket. It has no API, worker loop or business-service entry point.

The transport creates and inspects the container before starting it with document bytes on stdin.

Required settings are:

- No network (`--network=none`), plus the child's seccomp socket restrictions.
- Read-only root filesystem and no mounts or volumes.
- Non-root UID/GID 10002:10002.
- All Linux capabilities dropped.
- Privileged mode disabled and `no_new_privs` enabled.
- One PID/thread and one CPU.
- Configured memory cap with no additional swap allowance.
- No shared IPC namespace.
- A fixed parser command.

A generated container name is used for bounded forced cleanup, including timeout handling. A confirmed or indeterminate container creation followed by cleanup failure is reported explicitly.

If Docker becomes unreachable during cleanup, a stopped or running parser container may remain. Once the engine is available, inspect containers carrying the parser label:

```powershell
docker ps -a --filter "label=finagent.role=isolated-parser"
```

Do not treat a parsing timeout as proof that cleanup succeeded.

Docker Desktop and its daemon administrators are trusted infrastructure. The local engine receives document bytes through stdin; document bytes are not placed in CLI arguments. Parser output uses attached stdout. The container and its logs are removed on successful cleanup.

Image integrity, digest pinning, base-image scanning and release approval remain deployment duties. Rebuild the image whenever parser code or locked dependencies change. The image tag is for local development.

Do not mount the Docker socket into API or worker containers. Their Linux parser path applies seccomp directly without invoking Docker.

On native Linux hosts, install libseccomp2 through the host's package management before enabling document processing.

## Failure and evidence behavior

There is no unrestricted fallback.

Missing Docker/image/engine, invalid container controls, unsupported platforms or failed network/memory setup produce CRASHED/PARSE_FAILED results. Timeouts remain TIMEOUT.

Existing bounded retries, source preservation, extraction-failure states, audit and evidence-gap handling remain in force. No database migration is required.

Extraction limits record:

- `memory_limit_enforced`
- `network_limit_enforced`
- `process_limit_enforced`
- `isolation_method`

Unknown or failed controls are never reported as successful. Historical extraction records are not rewritten; the newer fields may be absent.

The API and worker Dockerfiles install libseccomp2. Running application images must be rebuilt to use the change. Installing source alone does not update an already-running container.

## Verification completed

Verification recorded on 2026-10-21:

| Check | Recorded result |
| --- | --- |
| Ruff lint | Passed |
| Ruff formatting | Passed; 210 files already formatted |
| Project mypy | Passed for 156 source files |
| Unit suite | 163 passed, 1 skipped |
| Full backend suite | 351 passed, 1 skipped, 1 browser test deselected |
| Dedicated parser image | Built successfully |
| Windows-host synthetic isolation check | Passed through the dedicated Docker Linux image |
| API and worker application images | Rebuilt and containers recreated |
| Deployed worker parser check | PARSED; network, memory and process flags true |
| Local service diagnostics | Passed |
| Browser regression | 1 passed in 24.93 seconds |
| Traceability | Regenerated with the latest backend and browser evidence |

Unit tests are included in the backend suite and must not be added to its total.

### Windows-host isolation check

The synthetic check confirmed:

- CSV parsing completed with required memory, network and process controls.
- Native socket-denial probes ran inside the isolated Linux parser.
- The parent retained the ability to create an unconnected socket.
- No database, model, connector or business action was invoked.

### Deployed worker check

The rebuilt FinAgent worker returned:

```text
status: PARSED
reason: None
network_limit_enforced: True
memory_limit_enforced: True
process_limit_enforced: True
isolation_method: linux-seccomp-v1
```

This verifies the Linux parser path inside the deployed development worker separately from the Windows-host Docker transport.

### Test coverage and skip

The new unit file contains 21 cases. Twenty passed in the Windows suite. One native Linux syscall test was skipped because the pytest host was Windows.

Successful Windows-host parser calls still require native denial probes inside their Linux containers. The skipped native Linux pytest case must not be reported as executed or passed in the Windows suite.

Coverage includes:

- Missing enforcement metadata.
- Failed isolation setup before document consumption.
- No Windows native parsing fallback.
- Container control drift.
- Timeout cleanup.
- Cleanup failure.
- Real parser transport outcomes.

The document upload integration test also passed its assertions for persisted isolation metadata.

During authoring, all 21 new cases and six additional Linux format/timeout smoke cases passed locally. That authoring evidence is separate from the Windows project results.

## Repeatable verification commands

Run from the FinAgent project directory:

```powershell
Set-Location -LiteralPath "C:\finagent-enterprise"
```

Build the dedicated parser image after relevant source or dependency changes:

```powershell
docker build -t finagent-parser:isolated-v1 -f apps/parser/Dockerfile .
```

Run the Windows-host synthetic boundary check:

```powershell
.\.venv\Scripts\python.exe scripts/check_parser_isolation.py
```

Run project checks:

```powershell
.\.venv\Scripts\python.exe scripts/tasks.py lint typecheck test-unit
.\.venv\Scripts\python.exe scripts/tasks.py deps-up test
```

Rebuild API and worker images after application changes:

```powershell
docker compose --profile app up -d --build --no-deps api worker
```

Check the deployed worker parser path:

```powershell
docker compose exec -T worker python -c "from finagent.documents.sandbox import run_parser, SandboxConfig; r=run_parser('text/csv', b'a,b\n1,2\n', SandboxConfig()); print({k:r.get(k) for k in ('status','reason','network_limit_enforced','memory_limit_enforced','process_limit_enforced','isolation_method')})"
```

Check services and browser regression:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\diagnose.ps1

.\.venv\Scripts\python.exe scripts/tasks.py test-browser
```

Refresh traceability after successful checks:

```powershell
.\.venv\Scripts\python.exe scripts/gen_traceability.py
```

These commands document the verification procedure. Completed checks do not need repeating unless relevant changes, failures or unresolved concerns justify another run.

## Remaining scope

- Broader filesystem isolation for the Linux host/worker parser path.
- Production workload and performance validation.
- Verification on each intended deployment platform.
- Release image integrity, digest pinning and vulnerability scanning.
- Operational handling of parser containers left behind during daemon outages.
- Production acceptance and requirement approval.

Native Windows document parsing remains disabled. Windows-host document processing requires the dedicated local Docker Linux image.

The local parser network-isolation implementation has verified development evidence. This does not establish that every aspect of SPR-017 is complete.

All source requirements remain DRAFT; none is APPROVED.

## Primary references

- Docker container execution: https://docs.docker.com/engine/containers/run/
- Docker resource constraints: https://docs.docker.com/engine/containers/resource_constraints/
- Linux seccomp: https://man7.org/linux/man-pages/man2/seccomp.2.html
- Linux no_new_privs: https://www.kernel.org/doc/html/latest/userspace-api/no_new_privs.html