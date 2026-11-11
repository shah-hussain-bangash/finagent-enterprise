"""Parser boundary checks: real Linux syscalls plus Windows container transport failures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from finagent.documents import parser_container, parsers, sandbox
from finagent.documents.parsers import ParseLimits
from finagent.documents.sandbox import SandboxConfig, run_parser


def _success() -> dict[str, Any]:
    return {
        "status": "PARSED",
        "parser": "test",
        "segments": [],
        "memory_limit_enforced": True,
        "network_limit_enforced": True,
        "process_limit_enforced": True,
        "isolation_method": "linux-seccomp-v1",
    }


@pytest.mark.req("SPR-017")
@pytest.mark.release_blocker
@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux seccomp runtime test; Windows transport tested separately"
)
def test_native_network_and_process_syscalls_are_denied() -> None:
    code = """
import ctypes, errno, json
from finagent.documents.network_guard import apply_network_limit, verify_network_limit
assert apply_network_limit()
assert verify_network_limit()
libc = ctypes.CDLL(None, use_errno=True)
libc.fork.restype = ctypes.c_int
ctypes.set_errno(0)
child = libc.fork()
if child == 0:
    import os
    os._exit(7)
assert child == -1 and ctypes.get_errno() == errno.EPERM
print(json.dumps({"network": True, "process": True}))
"""
    proc = subprocess.run(  # noqa: S603 - fixed interpreter and test-authored code
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert json.loads(proc.stdout) == {"network": True, "process": True}


@pytest.mark.req("SPR-017")
@pytest.mark.parametrize(
    "missing",
    ["memory_limit_enforced", "network_limit_enforced", "process_limit_enforced", "isolation_method"],
)
def test_parent_refuses_success_without_all_isolation_metadata(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    result = _success()
    result.pop(missing)
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
    monkeypatch.setattr(
        sandbox.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps(result).encode(), b""),
    )
    assert run_parser("text/csv", b"a\n1\n", SandboxConfig())["reason"] == "PARSER_ISOLATION_NOT_CONFIRMED"


@pytest.mark.req("SPR-017")
@pytest.mark.parametrize("memory", [False, True])
def test_worker_never_parses_after_isolation_setup_failure(
    monkeypatch: pytest.MonkeyPatch, memory: bool
) -> None:
    import io

    from finagent.documents import parse_worker

    header = {
        "size": 4,
        "memory_bytes": 64 * 1024 * 1024,
        "limits": ParseLimits().as_dict(),
        "media_type": "text/csv",
    }
    stream = io.BytesIO(json.dumps(header).encode() + b"\na\n1\n")
    out = io.StringIO()
    monkeypatch.setattr(parse_worker.sys, "stdin", SimpleNamespace(buffer=stream))
    monkeypatch.setattr(parse_worker.sys, "stdout", out)
    monkeypatch.setattr(parse_worker, "apply_network_limit", lambda: memory)
    monkeypatch.setattr(parse_worker, "verify_network_limit", lambda: True)
    monkeypatch.setattr(parse_worker, "apply_memory_limit", lambda n: False)
    parse = Mock(side_effect=AssertionError("parser must not run"))
    monkeypatch.setattr(parsers, "parse", parse)
    assert parse_worker.main() == 0
    result = json.loads(out.getvalue())
    assert result["status"] == "CRASHED"
    assert result["reason"] == (
        "PARSER_MEMORY_ISOLATION_UNAVAILABLE" if memory else "PARSER_NETWORK_ISOLATION_UNAVAILABLE"
    )
    assert not result["segments"]
    assert stream.read() == b"a\n1\n"
    parse.assert_not_called()


@pytest.mark.req("SPR-017")
def test_windows_has_no_native_parsing_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "sys", SimpleNamespace(platform="win32"))

    def unavailable(*args: Any) -> Any:
        raise parser_container.ParserContainerUnavailableError("PARSER_DOCKER_UNAVAILABLE")

    monkeypatch.setattr(sandbox, "run_container", unavailable)
    result = run_parser("text/csv", b"a\n1\n", SandboxConfig())
    assert result["status"] == "CRASHED" and result["reason"] == "PARSER_DOCKER_UNAVAILABLE"


def _inspection(memory: int) -> dict[str, Any]:
    return {
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Memory": memory,
            "MemorySwap": memory,
            "PidsLimit": 1,
            "IpcMode": "none",
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Config": {
            "User": "10002:10002",
            "Entrypoint": ["python"],
            "Cmd": ["-I", "-B", "-m", "finagent.documents.parse_worker"],
        },
        "Mounts": [],
    }


@pytest.mark.req("SPR-017")
@pytest.mark.parametrize(
    "fault",
    ["network", "mount", "memory", "pids", "privileged", "nnp", "user", "engine", "timeout", "cleanup", "ok"],
)
def test_container_controls_are_checked_before_payload_and_cleanup_is_required(
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    memory = 128 * 1024 * 1024
    record = _inspection(memory)
    if fault == "network":
        record["HostConfig"]["NetworkMode"] = "host"
    if fault == "mount":
        record["Mounts"] = [{"Source": "secret"}]
    if fault == "memory":
        record["HostConfig"]["Memory"] = 0
    if fault == "pids":
        record["HostConfig"]["PidsLimit"] = 0
    if fault == "privileged":
        record["HostConfig"]["Privileged"] = True
    if fault == "nnp":
        record["HostConfig"]["SecurityOpt"] = []
    if fault == "user":
        record["Config"]["User"] = "root"
    calls: list[tuple[list[str], bytes | None]] = []
    monkeypatch.setattr(parser_container.shutil, "which", lambda _: "docker.exe")

    def fake(
        argv: list[str], timeout: float, *, payload: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((argv, payload))
        assert argv[1:3] == ["--host", parser_container.LOCAL_ENGINE]
        config_dir = argv[4]
        assert json.loads(Path(config_dir, "config.json").read_text()) == {}
        operation = argv[5]
        if operation == "info":
            return subprocess.CompletedProcess(argv, 0, b"windows" if fault == "engine" else b"linux", b"")
        if operation == "inspect":
            return subprocess.CompletedProcess(argv, 0, json.dumps([record]).encode(), b"")
        if operation == "start" and fault == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        if operation == "rm" and fault == "cleanup":
            return subprocess.CompletedProcess(argv, 1, b"", b"private engine error")
        return subprocess.CompletedProcess(argv, 0, b"{}", b"")

    monkeypatch.setattr(parser_container, "_run", fake)
    if fault == "ok":
        parser_container.run_container(b"SYNTHETIC DOCUMENT", 10, memory)
    elif fault == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            parser_container.run_container(b"SYNTHETIC DOCUMENT", 10, memory)
    else:
        with pytest.raises(parser_container.ParserContainerUnavailableError):
            parser_container.run_container(b"SYNTHETIC DOCUMENT", 10, memory)
    sent = [payload for _, payload in calls if payload is not None]
    assert sent == ([b"SYNTHETIC DOCUMENT"] if fault in {"ok", "timeout", "cleanup"} else [])
    if fault != "engine":
        assert calls[-1][0][5] == "rm"
    assert all("SYNTHETIC DOCUMENT" not in " ".join(argv) for argv, _ in calls)


@pytest.mark.req("SPR-017", "FR-014")
@pytest.mark.parametrize(
    "media,data", [("text/csv", b"a,b\n1,2\n"), ("application/pdf", b"%PDF-1.4 garbage")]
)
def test_real_parser_transport_returns_enforced_metadata(media: str, data: bytes) -> None:
    result = run_parser(media, data, SandboxConfig())
    assert result["status"] == ("PARSED" if media == "text/csv" else "CORRUPT"), result
    assert result["network_limit_enforced"] is True
    assert result["memory_limit_enforced"] is True
    assert result["process_limit_enforced"] is True
