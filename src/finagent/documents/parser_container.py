"""Windows-host parser transport to local Docker Desktop Linux, never a remote daemon.

No socket/volume mount, host network, credentials, or business action is passed to the parser.
The Docker CLI is trusted host infrastructure; it is not made available inside API/worker images.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - fixed Docker CLI commands, never shell input
import tempfile
import time
import uuid
from pathlib import Path

IMAGE = "finagent-parser:isolated-v1"
LOCAL_ENGINE = "npipe:////./pipe/docker_engine"


class ParserContainerUnavailableError(Exception):
    """A required trusted container control could not be verified."""


def _run(
    argv: list[str], timeout: float, *, payload: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 # nosec B603 - private caller builds fixed Docker argv; bytes on stdin
        argv,
        input=payload,
        shell=False,
        capture_output=True,
        timeout=timeout,
        check=False,
        close_fds=True,
    )


def run_container(payload: bytes, timeout_s: float, memory_bytes: int) -> subprocess.CompletedProcess[bytes]:
    docker = shutil.which("docker.exe")
    if docker is None:
        raise ParserContainerUnavailableError("PARSER_DOCKER_UNAVAILABLE")
    deadline = time.monotonic() + timeout_s
    name = "finagent-parser-" + uuid.uuid4().hex
    # An empty client configuration plus explicit local named-pipe host avoids remote contexts,
    # credential helpers and user-configured proxies. Never auto-pull an image.
    with tempfile.TemporaryDirectory(prefix="finagent-parser-cli-") as config_dir:
        Path(config_dir, "config.json").write_text("{}", encoding="utf-8")
        base = [docker, "--host", LOCAL_ENGINE, "--config", config_dir]

        def remaining() -> float:
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(base, timeout_s)
            return left

        info = _run([*base, "info", "--format", "{{.OSType}}"], remaining())
        if info.returncode != 0 or info.stdout.strip() != b"linux":
            raise ParserContainerUnavailableError("PARSER_LINUX_ENGINE_UNAVAILABLE")
        created = False
        creation_uncertain = True
        try:
            create = _run(
                [
                    *base,
                    "create",
                    "--name",
                    name,
                    "--pull=never",
                    "--interactive",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges:true",
                    "--pids-limit=1",
                    "--memory",
                    str(memory_bytes),
                    "--memory-swap",
                    str(memory_bytes),
                    "--cpus=1",
                    "--user=10002:10002",
                    "--ipc=none",
                    "--label=finagent.role=isolated-parser",
                    "--entrypoint=python",
                    IMAGE,
                    "-I",
                    "-B",
                    "-m",
                    "finagent.documents.parse_worker",
                ],
                remaining(),
            )
            creation_uncertain = False
            created = create.returncode == 0
            if not created:
                raise ParserContainerUnavailableError("PARSER_CONTAINER_CREATE_FAILED")
            inspect = _run([*base, "inspect", name], remaining())
            if inspect.returncode != 0:
                raise ParserContainerUnavailableError("PARSER_CONTAINER_INSPECTION_FAILED")
            try:
                record = json.loads(inspect.stdout)[0]
                host = record["HostConfig"]
                config = record["Config"]
                valid = (
                    host["NetworkMode"] == "none"
                    and host["ReadonlyRootfs"] is True
                    and host["Privileged"] is False
                    and host["Memory"] == memory_bytes
                    and host["MemorySwap"] == memory_bytes
                    and host["PidsLimit"] == 1
                    and host["IpcMode"] == "none"
                    and host["CapDrop"] == ["ALL"]
                    and not host.get("CapAdd")
                    and not record.get("Mounts")
                    and "no-new-privileges:true" in host["SecurityOpt"]
                    and config["User"] == "10002:10002"
                    and config["Entrypoint"] == ["python"]
                    and config["Cmd"] == ["-I", "-B", "-m", "finagent.documents.parse_worker"]
                )
            except (ValueError, KeyError, IndexError, TypeError):
                valid = False
            if not valid:
                raise ParserContainerUnavailableError("PARSER_CONTAINER_CONTROLS_INVALID")
            # Attached stdin starts only after inspecting the created container's controls.
            return _run([*base, "start", "--attach", "--interactive", name], remaining(), payload=payload)
        finally:
            # Use the generated name even if create timed out after the daemon allocated it.
            # Cleanup is bounded independently of the parsing deadline.
            try:
                removed = _run([*base, "rm", "--force", "--volumes", name], 15)
                if (created or creation_uncertain) and removed.returncode != 0:
                    raise ParserContainerUnavailableError("PARSER_CONTAINER_CLEANUP_FAILED")
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ParserContainerUnavailableError("PARSER_CONTAINER_CLEANUP_FAILED") from exc
