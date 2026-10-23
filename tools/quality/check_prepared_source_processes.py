"""Verify real staged sources, native death and original receive evidence without creating Nodes.

The single-member target is declared retention context only. No election or online HA is claimed.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from check_content_stream_processes import original, target
from check_replica_content_processes import Processes, digest, save

ROOT = Path(__file__).resolve().parents[2]


def receive(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    """Read one bounded actual pipe response and require its original OS process identity."""
    if process.stdout is None:
        raise ValueError("actual original response pipe absent")
    raw = bytearray()
    deadline = time.monotonic() + 30
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b"\n" not in raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError("original actual source response deadline expired")
            chunk = os.read(process.stdout.fileno(), min(65536, 128 * 1024 + 1 - len(raw)))
            if not chunk or len(raw) + len(chunk) > 128 * 1024:
                raise ValueError("actual source response ended or exceeded its bound")
            raw.extend(chunk)
    value: dict[str, Any] = json.loads(raw)
    if value.get("pid") != process.pid:
        raise ValueError("actual source response belongs to a different OS process")
    return value


def ask(
    workers: Processes,
    process: subprocess.Popen[bytes],
    request: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    """Send exactly one original command; refusal never automatically re-executes a receive."""
    record = next(record for owned, record in workers.owned if owned is process)
    exchange: dict[str, Any] = {"request": request}
    record.setdefault("exchanges", []).append(exchange)
    if process.stdin is None or process.poll() is not None:
        raise ValueError("actual original command pipe is unavailable")
    process.stdin.write(json.dumps(request).encode() + b"\n")
    process.stdin.flush()
    value = receive(process)
    exchange["response"] = value
    if error is not None:
        assert error in value.get("error", ""), value
        return value
    result = value.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"actual original source command refused: {value}")
    return result


def run(output: Path, binary: Path, storage: Path, source_probe: Path) -> dict[str, Any]:
    """Independently check original SQL, native waits, original inodes and full bytes."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    paths = [binary, storage, source_probe, Path(__file__)]
    paths += list((ROOT / "core/artifact-store").rglob("*.rs"))
    paths += list((ROOT / "core/controller-replication").rglob("*.rs"))
    paths += [
        Path(__file__).with_name(name)
        for name in ("check_content_stream_processes.py", "check_replica_content_processes.py")
    ]
    identities = {str(path): digest(path) for path in paths}
    save(output / "source-identity.json", identities)
    workers = Processes(storage, output)
    result: dict[str, Any] = {
        "passed": False,
        "embodied_nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "controller_children": 0,
        "declared_retention_target_is_consensus": False,
    }
    try:
        payload = output / "actual-payload.bin"
        payload.write_bytes(os.urandom(2 * 1024 * 1024 + 13))
        store: dict[str, Any] = {
            "root": str(output / "receiver"),
            "profile": "a" * 64,
            "member": 73,
        }
        identity = workers.run({"action": "create", "store": store}, "receiver")["identity"]
        config: dict[str, Any] = {
            "receiver": store["root"],
            "profile": store["profile"],
            "member": store["member"],
            "source": str(output / "source"),
            "payload": str(payload),
            "upload_id": "actual-original-prepared-process",
            "resume": False,
        }
        process = workers.start(config, "source-original", binary)
        first = workers.ready(process)
        key = first["content"]
        assert first["public_cas"] is False
        assert key["content_digest"] == "sha256:" + digest(payload)
        assert key["byte_size"] == payload.stat().st_size
        source = Path(config["source"])
        staging = source / "staging" / f"{config['upload_id']}.partial"
        writer = source / ".writer.lock"
        inodes = {"staging": staging.stat().st_ino, "writer": writer.stat().st_ino}
        context = target(store, identity, key)

        def copy(command: str) -> dict[str, Any]:
            """Supply the complete declared original target without claiming network authority."""
            return {"action": "copy", "target": context, "command_id": command}

        ask(workers, process, {"action": "read"}, "reader is unavailable")
        ask(workers, process, copy("original-without-guard"), "was not found")
        unknown = original(Path(store["root"]), "original-without-guard")
        assert unknown is not None and unknown[1]["receipt"] is None
        registered = ask(workers, process, {"action": "register"})
        assert registered == {"registered": True, "public_cas": False}
        actual = ask(workers, process, {"action": "read"})
        assert actual == {"actual_digest": key["content_digest"], "byte_size": key["byte_size"]}
        replay = ask(workers, process, copy("original-without-guard"))
        assert replay["stream"]["original"] == unknown[1]
        assert original(Path(store["root"]), "original-without-guard") == unknown
        copied = ask(workers, process, copy("explicit-prepared-copy"))
        assert copied["stream"]["receipt"] is not None and copied["public_cas"] is False
        actual_file = (
            Path(store["root"])
            / "content-v1"
            / key["source_owner"][15:]
            / key["content_digest"][7:]
        )
        assert digest(actual_file) == digest(payload)
        assert actual_file.stat().st_size == payload.stat().st_size
        ask(workers, process, {"action": "revoke"})
        ask(workers, process, {"action": "read"}, "revoked")
        ask(workers, process, {"action": "append"}, "active source reader")
        fence = workers.start(
            {"action": "artifact-fence", "root": str(source)}, "writer-fence", source_probe
        )
        code, proof = workers.wait(fence)
        assert code == 0 and proof["result"]["archive_refused"] is True
        process.kill()
        code, _ = workers.wait(process)
        assert code == -signal.SIGKILL
        assert staging.stat().st_ino == inodes["staging"]
        assert digest(staging) == digest(payload)

        config["resume"] = True
        restarted = workers.start(config, "source-explicit-reopen", binary)
        second = workers.ready(restarted)
        assert second["writer"] == first["writer"] and second["content"] == key
        assert second["public_cas"] is False
        assert writer.stat().st_ino == inodes["writer"]
        ask(workers, restarted, {"action": "read"}, "reader is unavailable")
        ask(workers, restarted, copy("original-after-restart-without-guard"), "was not found")
        second_unknown = original(Path(store["root"]), "original-after-restart-without-guard")
        assert second_unknown is not None and second_unknown[1]["receipt"] is None
        ask(workers, restarted, {"action": "register"})
        ask(workers, restarted, copy("original-after-restart-without-guard"))
        assert (
            original(Path(store["root"]), "original-after-restart-without-guard") == second_unknown
        )
        repaired = ask(workers, restarted, copy("explicit-new-source-scope-repair"))
        assert repaired["stream"]["receipt"] is not None
        assert repaired["public_cas"] is False
        assert ask(workers, restarted, {"action": "finalize"})["public_cas"] is True
        ask(workers, restarted, {"action": "shutdown"})
        code, _ = workers.wait(restarted)
        assert code == 0
        assert original(Path(store["root"]), "original-without-guard") == unknown
        assert all(digest(Path(path)) == expected for path, expected in identities.items())
        result.update(
            passed=True,
            original_source=first,
            reopened_source=second,
            original_inodes=inodes,
            original_unknown=unknown[1],
            restart_unknown=second_unknown[1],
            actual_copy=copied,
            actual_repair=repaired,
        )
    finally:
        workers.cleanup()
        result["actual_processes"] = [record for _, record in workers.owned]
        save(output / "result.json", result)
    return result


def main() -> None:
    """Require exact frozen binaries and a fresh independently inspectable evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--storage-probe", type=Path, required=True)
    parser.add_argument("--source-probe", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.output,
        args.probe.resolve(strict=True),
        args.storage_probe.resolve(strict=True),
        args.source_probe.resolve(strict=True),
    )
    print(json.dumps({"passed": result["passed"], "result": str(args.output / "result.json")}))


if __name__ == "__main__":
    main()
