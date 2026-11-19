"""Hash or privately copy inherited inputs under one parent permit per bounded chunk."""

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

PRIVATE_COPY_SCHEMA = "roboguide.file-private-copy-start/v0.1"
PRIVATE_COPY_CHUNK_BYTES = 1024 * 1024
MAX_PRIVATE_COPY_BYTES = 1024**3


def emit(document: dict[str, Any]) -> None:
    """Write one bounded control reply without files, credentials or independent authority."""
    encoded = json.dumps(document, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > 16384:
        raise ValueError("file worker response exceeds its bound")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def apply_os_limits() -> dict[str, object]:
    """Apply the parent-declared CPU and address-space limits before reading any input."""
    try:
        cpu_seconds = int(os.environ["ROBOGUIDE_FILE_WORKER_CPU_SECONDS"])
        address_space = int(os.environ["ROBOGUIDE_FILE_WORKER_AS_BYTES"])
    except (KeyError, ValueError) as error:
        raise ValueError("original file worker OS quota declaration is unavailable") from error
    if not 1 <= cpu_seconds <= 300 or not 64 * 1024 * 1024 <= address_space <= 8 * 1024**3:
        raise ValueError("original file worker OS quota is outside its bound")
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
    cpu = resource.getrlimit(resource.RLIMIT_CPU)
    address = resource.getrlimit(resource.RLIMIT_AS)
    if cpu != (cpu_seconds, cpu_seconds) or address != (address_space, address_space):
        raise ValueError("actual original kernel quota differs from its declaration")
    return {
        "kind": "os-quota",
        "schema": "roboguide.file-worker-os-quota/v0.1",
        "pid": os.getpid(),
        "cpu_seconds": list(cpu),
        "address_space_bytes": list(address),
    }


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


def write_all(descriptor: int, chunk: bytes) -> None:
    """Write one permitted chunk completely without accepting a silent short write."""
    offset = 0
    while offset < len(chunk):
        written = os.write(descriptor, chunk[offset:])
        if written <= 0:
            raise OSError("private destination write made no progress")
        offset += written


def run_private_copy(command: dict[str, Any], quota: dict[str, object]) -> None:
    """Copy one exact authorized input into a parent-owned private temporary descriptor."""
    if set(command) != {"schema", "source", "destination_fd", "max_bytes"}:
        raise ValueError("private copy start command is not closed")
    source = command["source"]
    destination = command["destination_fd"]
    max_bytes = command["max_bytes"]
    if (
        not isinstance(source, dict)
        or set(source) != {"fd", "digest", "size"}
        or type(source["fd"]) is not int
        or type(source["size"]) is not int
        or type(destination) is not int
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= MAX_PRIVATE_COPY_BYTES
        or not 0 < source["size"] <= max_bytes
        or not isinstance(source["digest"], str)
        or re.fullmatch(r"sha256:[a-f0-9]{64}", source["digest"]) is None
    ):
        raise ValueError("private copy bounds or source evidence are invalid")
    source_fd = source["fd"]
    if source_fd <= 2 or destination <= 2 or source_fd == destination:
        raise ValueError("private copy descriptors are invalid")

    source_metadata = os.fstat(source_fd)
    destination_metadata = os.fstat(destination)
    source_flags = fcntl.fcntl(source_fd, fcntl.F_GETFL)
    destination_flags = fcntl.fcntl(destination, fcntl.F_GETFL)
    if (
        not stat.S_ISREG(source_metadata.st_mode)
        or source_metadata.st_size != source["size"]
        or source_flags & os.O_ACCMODE != os.O_RDONLY
    ):
        raise ValueError("private copy source is not an exact readonly regular input")
    if (
        not stat.S_ISREG(destination_metadata.st_mode)
        or destination_metadata.st_nlink != 0
        or destination_metadata.st_size != 0
        or destination_flags & os.O_ACCMODE not in (os.O_WRONLY, os.O_RDWR)
        or destination_flags & os.O_APPEND
        or os.lseek(destination, 0, os.SEEK_CUR) != 0
        or (source_metadata.st_dev, source_metadata.st_ino)
        == (destination_metadata.st_dev, destination_metadata.st_ino)
    ):
        raise ValueError("private copy destination is not an empty unlinked temporary output")
    os.lseek(source_fd, 0, os.SEEK_SET)

    emit(quota)
    emit(
        {
            "kind": "ready",
            "schema": PRIVATE_COPY_SCHEMA,
            "digest": source["digest"],
            "size": source["size"],
        }
    )
    hashed = hashlib.sha256()
    copied = 0
    while copied < source["size"]:
        permit()
        chunk = os.read(
            source_fd,
            min(PRIVATE_COPY_CHUNK_BYTES, source["size"] - copied),
        )
        if not chunk:
            raise ValueError("private copy source ended before its authorized size")
        if copied + len(chunk) > max_bytes:
            raise ValueError("private copy output exceeded its declared maximum")
        write_all(destination, chunk)
        hashed.update(chunk)
        copied += len(chunk)
    final_source = os.fstat(source_fd)
    final_destination = os.fstat(destination)
    digest = "sha256:" + hashed.hexdigest()
    if (
        final_source.st_size != source["size"]
        or copied != source["size"]
        or final_destination.st_size != copied
        or digest != source["digest"]
    ):
        raise ValueError("private copy bytes differ from their authorized identity")
    emit({"kind": "result", "digest": digest, "size": copied})


def run() -> None:
    """Run admitted hashing or private copy after the parent durably records its PID."""
    quota = apply_os_limits()
    raw = sys.stdin.buffer.readline(16385)
    if len(raw) > 16384 or not raw.endswith(b"\n"):
        raise ValueError("file worker requires a bounded original start command")
    command = json.loads(raw)
    if not isinstance(command, dict):
        raise ValueError("file worker start command is not closed")
    if command.get("schema") == PRIVATE_COPY_SCHEMA:
        run_private_copy(command, quota)
        return
    if set(command) != {"schema", "rounds", "inputs"}:
        raise ValueError("file worker start command is not closed")
    if (
        command["schema"] != "roboguide.file-process-start/v0.1"
        or type(command["rounds"]) is not int
        or not 1 <= command["rounds"] <= 16_777_216
        or not isinstance(command["inputs"], list)
        or not 1 <= len(command["inputs"]) <= 8
    ):
        raise ValueError("file worker contract or computation bounds are invalid")
    emit(quota)
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
