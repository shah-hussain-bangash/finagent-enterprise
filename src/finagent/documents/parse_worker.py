"""Child-process entry point for isolated parsing (SPR-017 "constrained permissions and limits").

Protocol (stdin): one JSON header line, then exactly `size` raw bytes. stdout: one JSON result.
Before touching the payload the child requires Linux seccomp network/process restrictions and
a native denial probe, then caps its own memory: RLIMIT_AS on POSIX, a Job Object with
a per-process memory limit and an active-process limit of 1 on Windows (so a parser cannot spawn
children either). `memory_limit_enforced` in the result says honestly whether that succeeded.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from finagent.documents.network_guard import apply_network_limit, verify_network_limit
from finagent.documents.parsers import ParseLimits


def apply_memory_limit(max_bytes: int) -> bool:
    if sys.platform == "win32":
        return _windows_job_limit(max_bytes)
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    except (ImportError, ValueError, OSError):
        return False
    return True


def _windows_job_limit(max_bytes: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basic),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    limit_process_memory, limit_active_process, extended_info_class = 0x100, 0x8, 9
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = limit_process_memory | limit_active_process
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = max_bytes
    if not kernel32.SetInformationJobObject(
        job, extended_info_class, ctypes.byref(info), ctypes.sizeof(info)
    ):
        return False
    return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))


def _emit(result: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


def _blocked(reason: str, *, network: bool, memory: bool) -> int:
    return _emit(
        {
            "status": "CRASHED",
            "reason": reason,
            "parser": "sandbox",
            "segments": [],
            "warnings": [],
            "memory_limit_enforced": memory,
            "network_limit_enforced": network,
            "process_limit_enforced": network,
            "isolation_method": "linux-seccomp-v1" if network else "unavailable",
        }
    )


def main() -> int:
    # The header is trusted parent-generated configuration; no document is parsed here.
    header: dict[str, Any] = json.loads(sys.stdin.buffer.readline(65537))
    network = apply_network_limit() and verify_network_limit()
    if not network:
        return _blocked("PARSER_NETWORK_ISOLATION_UNAVAILABLE", network=False, memory=False)
    enforced = apply_memory_limit(int(header["memory_bytes"]))
    if not enforced:
        return _blocked("PARSER_MEMORY_ISOLATION_UNAVAILABLE", network=True, memory=False)
    size = int(header["size"])
    if size < 0:
        return _blocked("PARSER_INPUT_LIMIT", network=True, memory=True)
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        result: dict[str, Any] = {"status": "CORRUPT", "reason": "TRUNCATED_INPUT", "parser": "sandbox"}
    else:
        # Import third-party libraries only after restrictions are established.
        from finagent.documents.parsers import parse

        result = parse(str(header["media_type"]), data, ParseLimits(**header["limits"]))
    result["memory_limit_enforced"] = True
    result["network_limit_enforced"] = True
    result["process_limit_enforced"] = True
    result["isolation_method"] = "linux-seccomp-v1"
    return _emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
