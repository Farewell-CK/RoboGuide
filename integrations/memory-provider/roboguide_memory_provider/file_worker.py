"""Hash only inherited readonly inputs, taking one parent permit before each bounded read."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import resource
import stat
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any


def emit(document: dict[str, Any]) -> None:
    """Write one bounded control reply without files, credentials or independent authority."""
    encoded = json.dumps(document, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > 16384:
        raise ValueError("file worker response exceeds its bound")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def apply_os_limits() -> None:
    """Apply the parent-declared CPU and address-space limits before reading any input."""
    try:
        cpu_seconds = int(os.environ["ROBOGUIDE_FILE_WORKER_CPU_SECONDS"])
        address_space = int(os.environ["ROBOGUIDE_FILE_WORKER_AS_BYTES"])
    except KeyError:
        # Direct protocol fixtures predate the quota envelope; the configured ProcessFileJobs
        # parent always supplies both values before admitting a production child.
        return
    except ValueError as error:
        raise ValueError("original file worker OS quota declaration is unavailable") from error
    if not 1 <= cpu_seconds <= 300 or not 64 * 1024 * 1024 <= address_space <= 8 * 1024**3:
        raise ValueError("original file worker OS quota is outside its bound")
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))


def permit() -> None:
    """Stop on EOF or refusal; a surviving child cannot acquire permission by itself."""
    while True:
        emit({"kind": "permit"})
        raw = sys.stdin.buffer.readline(1025)
        if not raw or len(raw) > 1024 or not raw.endswith(b"\n"):
            raise InterruptedError("original parent compute permission ended")
        budget = json.loads(raw)
        if not isinstance(budget, dict) or set(budget) != {
            "boot_id",
            "time_namespace",
            "received_lower_ms",
            "deadline_lower_ms",
        }:
            raise ValueError("original child read budget is not closed")
        lower, deadline = budget["received_lower_ms"], budget["deadline_lower_ms"]
        if (
            any(type(value) is not int for value in (lower, deadline))
            or not 0 < deadline - lower <= 100
        ):
            raise ValueError("original child read budget exceeds its bound")
        if (
            Path("/proc/sys/kernel/random/boot_id").read_text().strip() != budget["boot_id"]
            or str(Path("/proc/self/ns/time").readlink()) != budget["time_namespace"]
        ):
            raise InterruptedError("original child read budget changed kernel time domain")
        uptime = Path("/proc/uptime").read_text().split()[0]
        matched = re.fullmatch(r"([0-9]+)\.([0-9]{1,3})", uptime)
        if matched is None:
            raise ValueError("original child kernel clock unavailable")
        whole, fraction = matched.groups()
        quantum = 10 ** (3 - len(fraction))
        now = int(whole) * 1000 + int(fraction) * quantum
        if now < lower:
            raise InterruptedError("original child kernel clock went backwards")
        if now + quantum < deadline:
            return


def run() -> None:
    """Perform real CPU hashing after the parent has durably recorded the original PID."""
    apply_os_limits()
    raw = sys.stdin.buffer.readline(16385)
    if len(raw) > 16384 or not raw.endswith(b"\n"):
        raise ValueError("file worker requires a bounded original start command")
    command = json.loads(raw)
    if not isinstance(command, dict) or set(command) != {"schema", "rounds", "inputs"}:
        raise ValueError("file worker start command is not closed")
    if (
        command["schema"] != "roboguide.file-process-start/v0.1"
        or type(command["rounds"]) is not int
        or not 1 <= command["rounds"] <= 16_777_216
        or not isinstance(command["inputs"], list)
        or not 1 <= len(command["inputs"]) <= 8
    ):
        raise ValueError("file worker contract or computation bounds are invalid")
    with ExitStack() as stack:
        streams = []
        descriptors: set[int] = set()
        for item in command["inputs"]:
            if not isinstance(item, dict) or set(item) != {"fd", "digest", "size"}:
                raise ValueError("file worker input is not closed")
            descriptor = item["fd"]
            if (
                type(descriptor) is not int
                or descriptor <= 2
                or descriptor in descriptors
                or type(item["size"]) is not int
                or item["size"] < 0
                or not isinstance(item["digest"], str)
                or re.fullmatch(r"sha256:[a-f0-9]{64}", item["digest"]) is None
            ):
                raise ValueError("file worker input descriptor or evidence is invalid")
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != item["size"]
                or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
            ):
                raise ValueError("file worker accepts only exact readonly regular inputs")
            descriptors.add(descriptor)
            streams.append(stack.enter_context(os.fdopen(descriptor, "rb", buffering=0)))
        digests: list[str] = []
        for _ in range(command["rounds"]):
            permit()
            digests = []
            for item, stream in zip(command["inputs"], streams, strict=True):
                stream.seek(0)
                hashed = hashlib.sha256()
                size = 0
                while True:
                    permit()
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > item["size"]:
                        raise ValueError("file worker input grew beyond its original size")
                    hashed.update(chunk)
                digest = "sha256:" + hashed.hexdigest()
                if size != item["size"] or digest != item["digest"]:
                    raise ValueError("file worker bytes differ from original manifest")
                digests.append(digest)
        emit({"kind": "result", "digests": digests})


def main() -> int:
    """Exit with an actual OS status; terminal rows and cancel flags cannot supply this result."""
    try:
        run()
    except (ValueError, KeyError, TypeError, OSError) as error:
        try:
            emit({"kind": "error", "reason": str(error)[:1024]})
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
