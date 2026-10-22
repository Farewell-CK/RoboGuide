"""Exercise member-local content files/SQL/processes; never claim online quorum or device facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def save(path: Path, value: object) -> None:
    """Persist actual bounded structured observations without replacing historical runs."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path: Path) -> str:
    """Hash real files incrementally rather than inferring bytes from a receipt flag."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Processes:
    """Retain original actual PIDs and native wait results even if an assertion later fails."""

    def __init__(self, binary: Path, output: Path) -> None:
        """Bind the exact maintained executable and a new owned evidence directory."""
        self.binary = binary
        self.output = output
        self.owned: list[tuple[subprocess.Popen[bytes], dict[str, Any]]] = []

    def start(self, request: dict[str, Any], label: str) -> subprocess.Popen[bytes]:
        """Persist exact input before spawning its actual independent process."""
        path = self.output / f"request-{len(self.owned):03d}-{label}.json"
        save(path, request)
        process = subprocess.Popen(
            [str(self.binary), str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        self.owned.append((process, {"pid": process.pid, "label": label, "request": str(path)}))
        return process

    def wait(
        self, process: subprocess.Popen[bytes], finish: bool = False
    ) -> tuple[int, dict[str, Any]]:
        """Reap this exact owned process; retain actual errors and killed-process exit codes."""
        try:
            stdout, stderr = process.communicate(b"finish\n" if finish else None, timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
        if len(stdout) > 128 * 1024 or len(stderr) > 128 * 1024:
            raise ValueError("actual probe response exceeded the explicit evidence bound")
        record = next(record for owned, record in self.owned if owned is process)
        record.update(
            returncode=process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
        lines = stdout.splitlines()
        value: dict[str, Any] = json.loads(lines[-1]) if lines else {}
        record["response"] = value
        return process.returncode, value

    def run(self, request: dict[str, Any], label: str) -> dict[str, Any]:
        """Run an expected successful original operation and require its actual native zero exit."""
        code, value = self.wait(self.start(request, label))
        if code != 0 or "result" not in value:
            raise ValueError(f"actual {label} refused: {value}")
        result: dict[str, Any] = value["result"]
        return result

    def refuse(self, request: dict[str, Any], label: str, message: str) -> None:
        """Require the specific refusal; unrelated parse or path errors do not satisfy it."""
        code, value = self.wait(self.start(request, label))
        if code == 0 or message not in value.get("error", ""):
            raise ValueError(f"actual {label} did not refuse for {message!r}: {value}")

    def ready(self, process: subprocess.Popen[bytes]) -> dict[str, Any]:
        """Read one bounded real readiness line while the original worker remains alive."""
        if process.stdout is None:
            raise ValueError("actual process stdout is unavailable")
        deadline = time.monotonic() + 60
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError("actual original process readiness deadline expired")
                chunk = os.read(process.stdout.fileno(), 32 * 1024 + 1 - len(data))
                if not chunk:
                    raise ValueError("actual process ended before readiness")
                data.extend(chunk)
                if len(data) > 32 * 1024:
                    raise ValueError("actual readiness evidence exceeded its bound")
        ready: dict[str, Any] = json.loads(data)
        if ready.get("pid") != process.pid or ready.get("ready") is not True:
            raise ValueError("readiness did not report the original actual process")
        next(record for owned, record in self.owned if owned is process)["ready"] = ready
        return ready

    def cleanup(self) -> None:
        """Reap every original worker without turning cancellation into success evidence."""
        for process, record in self.owned:
            if "returncode" not in record:
                if process.poll() is None:
                    process.kill()
                self.wait(process)


def operation(root: Path, command: str) -> dict[str, Any] | None:
    """Independently read original actual copy SQL without using a product success response."""
    path = root / "content.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as database:
        rows = database.execute("SELECT record FROM content_operations").fetchall()
    for (raw,) in rows:
        value: dict[str, Any] = json.loads(raw)
        if value["intent"]["command_id"] == command:
            return value
    return None


def stop_after_intent(process: subprocess.Popen[bytes], root: Path, command: str) -> dict[str, Any]:
    """Observe a real durable intent, then actually SIGSTOP before its copy receipt commits."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("actual copier ended before the retained intent fault window")
        try:
            row = operation(root, command)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            continue
        if row is not None and row["receipt"] is None:
            os.kill(process.pid, signal.SIGSTOP)
            row = operation(root, command)
            if row is None or row["receipt"] is not None:
                raise ValueError("actual copier passed the original receipt fault window")
            return row
        time.sleep(0.001)
    raise ValueError("actual copy intent did not become durable within the original wait bound")


def run(binary: Path, output: Path, profile: str, member: int, payload_mib: int) -> dict[str, Any]:
    """Prove local Unknown/lease/byte boundaries without contacting Raft or creating Nodes."""
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("the actual maintained content process probe must already be built")
    if len(profile) != 64 or any(character not in "0123456789abcdef" for character in profile):
        raise ValueError("the explicit original software profile must be a full lowercase digest")
    if member <= 0 or not 32 <= payload_mib <= 512:
        raise ValueError("explicit software member/payload bounds are invalid")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    workers = Processes(binary, output)
    result: dict[str, Any] = {
        "schema": "roboguide.replica-content-process-check/v0.1",
        "scope": "member-local storage only; no online quorum or application authority",
        "probe_sha256": digest(binary),
        "checker_sha256": digest(Path(__file__).resolve()),
        "source_files": {
            str(path.relative_to(ROOT)): digest(path)
            for path in sorted(
                [
                    *(ROOT / "core/controller-replication/src/content_store").glob("*.rs"),
                    ROOT / "core/controller-replication/examples/content_store_probe.rs",
                    ROOT / "core/controller-replication/src/content.rs",
                    ROOT / "core/controller-replication/src/lib.rs",
                ]
            )
        },
        "profile": profile,
        "member": member,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "passed": False,
    }
    try:
        payload = output / "actual-payload.bin"
        block = hashlib.sha256(b"explicit actual file-only storage fault fixture").digest() * 32768
        with payload.open("xb") as stream:
            for _ in range(payload_mib):
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        source = output / "original-source"
        content = workers.run(
            {"action": "source", "root": str(source), "payload": str(payload)}, "source"
        )["content"]
        if content["content_digest"] != "sha256:" + digest(payload):
            raise ValueError(
                "actual original source bytes differ from the independent payload hash"
            )
        target = output / "member-content"
        store = {"root": str(target), "profile": profile, "member": member}
        identity = workers.run({"action": "create", "store": store}, "create")["identity"]
        result["original_content"] = content
        result["actual_receiver"] = identity
        base = {"store": store, "content": content}
        copy = {
            "action": "copy",
            **base,
            "source": str(source),
            "authority": profile,
            "budget_ms": 120000,
        }
        inspect = {
            "action": "inspect",
            **base,
            "copies": ["interrupted-copy", "sql-failed-copy", "explicit-storage-repair"],
            "readers": [],
        }
        wrong_owner = dict(content, source_owner="artifact-store:" + "f" * 64)
        workers.refuse(
            dict(copy, content=wrong_owner, command_id="foreign-owner"),
            "foreign-owner",
            "another original Artifact namespace",
        )
        initial = workers.run(inspect, "initial")
        if initial["references"] or initial["actual_bytes"]:
            raise ValueError(
                "a source label created target availability or retention without an actual copy"
            )

        original = workers.start(dict(copy, command_id="interrupted-copy"), "interrupted-copy")
        interrupted = stop_after_intent(original, target, "interrupted-copy")
        save(output / "actual-interrupted-intent.json", interrupted)
        workers.refuse(inspect, "live-copy-target-lease", "still active")
        if (
            workers.run(
                {"action": "artifact-fence", "root": str(source)}, "live-copy-source-lease"
            )["archive_refused"]
            is not True
        ):
            raise ValueError("the actual copier did not retain its original source writer lease")
        original.kill()
        killed, _ = workers.wait(original)
        if killed != -signal.SIGKILL:
            raise ValueError("original killed copier lacks its actual native wait result")
        unknown = workers.run(inspect, "original-copy-unknown")
        if unknown["copies"][0]["status"]["receipt"] is not None or unknown["actual_bytes"]:
            raise ValueError("actual interrupted copy invented completion or availability")
        staging = {
            path.name: (path.stat().st_ino, path.stat().st_size)
            for path in (target / "staging-v1").iterdir()
        }
        replay = workers.run(dict(copy, command_id="interrupted-copy"), "original-copy-replay")[
            "copy"
        ]
        if replay != unknown["copies"][0]["status"] or staging != {
            path.name: (path.stat().st_ino, path.stat().st_size)
            for path in (target / "staging-v1").iterdir()
        }:
            raise ValueError("original uncertain copy retry performed new byte effects")
        ended_copy = workers.run(
            {"action": "retire-copy", "store": store, "command_id": "interrupted-copy"},
            "copy-process-end",
        )["copy"]
        if ended_copy["receipt"] is not None or ended_copy["process_end"] is None:
            raise ValueError("copier process-end observation fabricated an original byte receipt")
        if (
            workers.run(
                {"action": "retire-copy", "store": store, "command_id": "interrupted-copy"},
                "copy-process-end-replay",
            )["copy"]
            != ended_copy
        ):
            raise ValueError("original copier end observation renewed its immutable evidence")

        sql_failed = workers.start(dict(copy, command_id="sql-failed-copy"), "sql-failed-copy")
        stop_after_intent(sql_failed, target, "sql-failed-copy")
        with closing(sqlite3.connect(target / "content.sqlite3", timeout=1)) as fault:
            fault.execute("BEGIN IMMEDIATE")
            os.kill(sql_failed.pid, signal.SIGCONT)
            code, failure = workers.wait(sql_failed)
            fault.rollback()
        if code == 0 or "locked" not in failure.get("error", ""):
            raise ValueError(f"actual SQL failure was not observed: {failure}")
        after_sql = workers.run(inspect, "actual-sql-unknown")
        if after_sql["copies"][1]["status"]["receipt"] is not None or after_sql["actual_bytes"]:
            raise ValueError("uncommitted final bytes became accepted availability")
        if (
            workers.run(dict(copy, command_id="sql-failed-copy"), "sql-unknown-replay")["copy"]
            != after_sql["copies"][1]["status"]
        ):
            raise ValueError("original SQL-uncertain copy renewed its operation")
        repair = workers.run(
            dict(copy, command_id="explicit-storage-repair"), "explicit-storage-repair"
        )["copy"]
        if repair["receipt"] is None:
            raise ValueError(
                "new explicit storage operation did not persist its actual byte receipt"
            )
        qualified = (
            target / "content-v1" / content["source_owner"][15:] / content["content_digest"][7:]
        )
        if digest(qualified) != digest(payload):
            raise ValueError("actual qualified receiver bytes differ from original source bytes")
        after_repair = workers.run(inspect, "verify-actual-repaired-bytes")
        if (
            after_repair["actual_bytes"] is not True
            or after_repair["copies"][0]["status"] != ended_copy
            or after_repair["copies"][1]["status"] != after_sql["copies"][1]["status"]
        ):
            raise ValueError("independent byte repair rewrote an earlier original Unknown")
        workers.run(
            {"action": "retire-copy", "store": store, "command_id": "sql-failed-copy"},
            "sql-copy-process-end",
        )

        read = {
            "action": "read",
            **base,
            "authority": profile,
            "budget_ms": 120000,
            "prefix": 4096,
            "hold": True,
            "reader_id": "interrupted-reader",
        }
        reader = workers.start(read, "interrupted-reader")
        ready = workers.ready(reader)
        if ready["bytes_read"] != 4096 or ready["reader"]["receipt"] is not None:
            raise ValueError(
                "actual original reader readiness did not preserve an unresolved original operation"
            )
        workers.refuse(inspect, "managed-reader-retains-original-lease", "still active")
        reader.kill()
        if workers.wait(reader)[0] != -signal.SIGKILL:
            raise ValueError("actual original reader lacks its native killed wait result")
        inspect["readers"] = ["interrupted-reader", "complete-reader", "partial-reader"]
        reader_unknown = workers.run(inspect, "reader-unknown-after-restart")
        if reader_unknown["readers"][0]["status"]["receipt"] is not None or not any(
            pin["kind"] == "reader" for pin in reader_unknown["references"]
        ):
            raise ValueError(
                "reader process death fabricated close or prematurely released its pin"
            )
        workers.refuse(
            dict(read, hold=False), "original-reader-never-reopens", "observe it without reopening"
        )
        ended_reader = workers.run(
            {"action": "retire-reader", "store": store, "reader_id": "interrupted-reader"},
            "reader-process-end",
        )["reader"]
        if ended_reader["receipt"] is not None or ended_reader["process_end"] is None:
            raise ValueError(
                "original process absence fabricated an actual read-completion receipt"
            )
        complete = workers.run(
            dict(read, reader_id="complete-reader", prefix=None, hold=False), "actual-complete-read"
        )["reader_receipt"]
        partial = workers.run(
            dict(read, reader_id="partial-reader", prefix=13, hold=False), "actual-partial-close"
        )["reader_receipt"]
        if (
            complete["complete"] is not True
            or complete["observed_digest"] != content["content_digest"]
            or complete["bytes_read"] != content["byte_size"]
            or partial["complete"]
            or partial["bytes_read"] != 13
        ):
            raise ValueError("actual complete and partial reader evidence was conflated")
        result["original_copy_end"] = ended_copy
        result["original_reader_end"] = ended_reader
        result["actual_complete_reader"] = complete
        result["actual_partial_reader"] = partial

        holder = workers.start({"action": "hold", "store": store}, "original-store-clone")
        workers.ready(holder)
        workers.refuse(inspect, "original-clone-retains-lease", "still active")
        alias = output / "hardlink-alias"
        alias.mkdir(mode=0o700)
        (alias / ".writer.lock").touch(mode=0o600)
        os.link(target / "content.sqlite3", alias / "content.sqlite3")
        workers.refuse(
            dict(inspect, store=dict(store, root=str(alias))),
            "actual-sql-inode-alias",
            "still active",
        )
        if workers.wait(holder, finish=True)[0] != 0:
            raise ValueError("the original store clone did not end through its actual wait")
        workers.refuse(
            dict(inspect, store=dict(store, root=str(alias))),
            "alias-no-replacement-authority",
            "No such file",
        )
        fence = workers.run(
            {"action": "artifact-fence", "root": str(target)}, "old-artifact-writer-fence"
        )
        if fence != {"default_refused": True, "archive_refused": True}:
            raise ValueError(
                "an ordinary Artifact writer acquired private infrastructure authority"
            )

        qualified.chmod(0o600)
        with qualified.open("r+b") as stream:
            original_byte = stream.read(1)
            stream.seek(0)
            stream.write(bytes([original_byte[0] ^ 1]))
            stream.flush()
            os.fsync(stream.fileno())
        corrupted = workers.run(inspect, "actual-byte-corruption")
        if corrupted["actual_bytes"] or corrupted["copies"][2]["status"] != repair:
            raise ValueError("historical copy ACK concealed current byte corruption")
        workers.refuse(
            dict(read, reader_id="corrupted-reader", hold=False),
            "corrupt-reader-refuses",
            "actual replica content hash differs",
        )
        corrupt_end = workers.run(
            {"action": "retire-reader", "store": store, "reader_id": "corrupted-reader"},
            "corrupt-reader-process-end",
        )["reader"]
        if corrupt_end["receipt"] is not None:
            raise ValueError("corrupted reader acquired a byte completion receipt")
        with closing(sqlite3.connect(target / "content.sqlite3")) as database:
            database.execute("DELETE FROM content_readers")
            database.commit()
        workers.refuse(
            inspect, "missing-mandatory-reader-rows", "mandatory head lost or changed journal rows"
        )
        if not (target / ".retention-owner.json").is_file():
            raise ValueError("failed original-store validation removed its authority marker")
        result.update(
            actual_sigkill_unknown=True,
            actual_sql_commit_failure=True,
            independent_storage_repair=True,
            actual_managed_reader_lease=True,
            actual_inodes_and_old_writer_fences=True,
            original_unknown_and_budgets_preserved=True,
            actual_corruption_not_hidden_by_history=True,
            missing_mandatory_rows_refused=True,
            passed=True,
        )
    finally:
        workers.cleanup()
        result["processes"] = [record for _, record in workers.owned]
        save(output / "summary.json", result)
    return result


def main() -> None:
    """Run explicit local infrastructure storage checks with a separately built maintained probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--member", type=int, required=True)
    parser.add_argument("--payload-mib", type=int, default=128)
    arguments = parser.parse_args()
    result = run(
        arguments.probe.resolve(),
        arguments.output.resolve(),
        arguments.profile,
        arguments.member,
        arguments.payload_mib,
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "nodes": 0,
                "devices": 0,
                "summary": str(arguments.output.resolve() / "summary.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
