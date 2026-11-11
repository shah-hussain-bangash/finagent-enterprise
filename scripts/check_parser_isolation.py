"""Read-only synthetic parser boundary check; no database or remote request."""

from __future__ import annotations

import socket

from finagent.documents.sandbox import SandboxConfig, run_parser


def main() -> int:
    # Creating an unconnected parent socket verifies that controls are child-only. No traffic.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
        pass
    result = run_parser("text/csv", b"label,value\nSYNTHETIC,1\n", SandboxConfig())
    if result.get("status") != "PARSED" or any(
        result.get(key) is not True
        for key in ("memory_limit_enforced", "network_limit_enforced", "process_limit_enforced")
    ):
        print("FAIL: isolated synthetic parsing did not complete.")
        print("Status:", result.get("status"))
        print("Reason:", result.get("reason"))
        return 1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
        pass
    print("PASS: synthetic CSV parsed with required memory, network and process controls.")
    print("PASS: native socket-denial probes ran inside the isolated parser.")
    print("PASS: the parent can still create an unconnected socket; no network traffic was sent.")
    print("No database, model, connector or business action was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
