"""Verify actual original parent adoption through the product CLI, native locks and owned waits."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import sqlite3
import stat
import subprocess
import time
from contextlib import closing
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, cast

from check_replica_content_processes import Processes, digest, save

HISTORY = "content_parent_adoptions"
COMMIT = "content_parent_adoption_commit"


class PayloadReadBarrier:
    """Own a real Linux write lease that blocks opening this private immutable CAS file."""

    def __init__(self, path: Path) -> None:
        """Retain only the actual owned path; opening/lease admission happens in the context."""
        self.path = path
        self.descriptor: int | None = None
        self.previous: Any = None
        self.leased = False
        self.signals: list[int] = []
        self.original_mode = stat.S_IMODE(path.stat().st_mode)

    def observe_break(self, signum: int, frame: FrameType | None) -> None:
        """Record the actual kernel SIGIO for an attempted open of the leased file."""
        self.signals.append(signum)

    def __enter__(self) -> PayloadReadBarrier:
        """Acquire the real FD/lease and restore original mode before starting any product child."""
        try:
            if not self.original_mode & stat.S_IWUSR:
                self.path.chmod(self.original_mode | stat.S_IWUSR)
            try:
                self.descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
            finally:
                self.path.chmod(self.original_mode)
            self.previous = signal.signal(signal.SIGIO, self.observe_break)
            fcntl.fcntl(self.descriptor, fcntl.F_SETOWN, os.getpid())
            fcntl.fcntl(self.descriptor, fcntl.F_SETLEASE, fcntl.F_WRLCK)
            self.leased = True
            return self
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Release the actual lease/FD and original signal handler; preserve CAS bytes."""
        if self.descriptor is not None:
            try:
                if self.leased:
                    fcntl.fcntl(self.descriptor, fcntl.F_SETLEASE, fcntl.F_UNLCK)
            finally:
                os.close(self.descriptor)
                self.descriptor = None
                self.leased = False
                if self.previous is not None:
                    signal.signal(signal.SIGIO, self.previous)
                    self.previous = None

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the real owned barrier on success/error; propagate every original failure."""
        self.close()


def payload_path(member: dict[str, Any]) -> Path:
    """Resolve a retained CAS receipt and verify its original device/inode before leasing."""
    root = Path(member["root"]) / "content"
    with closing(
        sqlite3.connect((root / "content.sqlite3").as_uri() + "?mode=ro", uri=True)
    ) as sql:
        row = sql.execute(
            "SELECT record FROM content_objects ORDER BY content_key LIMIT 1"
        ).fetchone()
    if row is None:
        raise ValueError("actual adoption fixture has no original content receipt to lease")
    receipt = json.loads(row[0])
    key = receipt["content"]
    path: Path = (
        root
        / "content-v1"
        / key["source_owner"].removeprefix("artifact-store:")
        / key["content_digest"].removeprefix("sha256:")
    )
    actual = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
        receipt["file_device"],
        receipt["file_inode"],
    ):
        raise ValueError("original leased CAS file differs from its actual durable receipt")
    return path


def stop_owned_child(child: subprocess.Popen[bytes]) -> int:
    """Wait the actual owned process stop event before releasing the kernel read barrier."""
    os.kill(child.pid, signal.SIGSTOP)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pid, status = os.waitpid(child.pid, os.WUNTRACED | os.WNOHANG)
        if pid:
            if os.WIFSTOPPED(status):
                return os.WSTOPSIG(status)
            child.returncode = os.waitstatus_to_exitcode(status)
            raise ValueError(f"original process exited before actual stop: {child.returncode}")
        time.sleep(0.001)
    raise ValueError("original process stop observation deadline expired")


def declaration(member: dict[str, Any]) -> dict[str, Any]:
    """Declare only the original existing infrastructure storage, never a Node/device inventory."""
    return {
        "content_root": str(Path(member["root"]) / "content"),
        "profile_identity": member["profile"],
        "member": member["member"],
    }


def request(member: dict[str, Any], command: str, budget: int = 300_000) -> dict[str, Any]:
    """Freeze one explicit original maintenance command before the actual product process starts."""
    return {
        "operation": "adopt-parent",
        "member": declaration(member),
        "parent_sql": str(Path(member["root"]) / "replica.sqlite3"),
        "operation_id": command,
        "budget_ms": budget,
    }


def parent(member: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Read raw original SQL and close its actual connection before product restart."""
    path = Path(member["root"]) / "replica.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as sql:
        version = sql.execute("PRAGMA user_version").fetchone()[0]
        values = {
            key: json.loads(raw)
            for key, raw in sql.execute(
                "SELECT key,value FROM replica_v1_meta WHERE key IN (?,?)", (HISTORY, COMMIT)
            )
        }
    return version, values


def business(member: dict[str, Any]) -> dict[str, Any]:
    """Compare exact original business values, excluding declared local bookkeeping."""
    path = Path(member["root"]) / "replica.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        result: dict[str, Any] = {
            "meta": sql.execute(
                "SELECT key,value FROM replica_v1_meta WHERE key NOT IN (?,?,?,?) ORDER BY key",
                (HISTORY, COMMIT, "content_producer_heads", "content_parent_adoption_ids"),
            ).fetchall()
        }
        for table in ("commands", "events", "log", "snapshot"):
            result[table] = sql.execute(f"SELECT * FROM replica_v1_{table} ORDER BY 1").fetchall()
    return result


def legacy(member: dict[str, Any]) -> dict[str, str]:
    """Retain each exact old raw pin, without assigning unmatched root references to this parent."""
    path = Path(member["root"]) / "content/content.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as sql:
        ids = json.loads(
            sql.execute("SELECT value FROM metadata WHERE key='legacy_producer_ids'").fetchone()[0]
        )
        return {
            identity: sql.execute(
                "SELECT record FROM content_references WHERE reference_id=?", (identity,)
            ).fetchone()[0]
            for identity in ids
        }


def producers(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Read complete actual root producer evidence independently of product response JSON."""
    path = Path(member["root"]) / "content/content.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)) as sql:
        return [
            json.loads(raw)
            for (raw,) in sql.execute("SELECT record FROM content_producers ORDER BY operation_id")
        ]


def wait_intent(
    child: subprocess.Popen[bytes], member: dict[str, Any], command: str
) -> dict[str, Any]:
    """Observe only this real child's durable first intent within a fixed original fault window."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ValueError(
                "original adoption process exited before its actual intent fault window"
            )
        with (Path(member["root"]) / "replica.sqlite3").open("rb") as main:
            header = main.read(20)
        if header[18:20] != b"\x01\x01":
            time.sleep(0.002)
            continue
        try:
            _, values = parent(member)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise
            time.sleep(0.002)
            continue
        for row in values.get(HISTORY, []):
            if row["intent"]["operation_id"] == command:
                if row["intent"]["budget"]["pid"] != child.pid or row["publication"] is not None:
                    raise ValueError(
                        "original adoption intent has another PID or already completed"
                    )
                actual = (Path(member["root"]) / "replica.sqlite3").stat()
                if row["intent"]["sql_identity"] != [actual.st_dev, actual.st_ino]:
                    raise ValueError("original adoption did not bind its actual source SQL inode")
                return cast(dict[str, Any], row)
        time.sleep(0.002)
    raise ValueError("original adoption durable intent observation expired")


def unknown(
    cli: Processes,
    member: dict[str, Any],
    command: str,
    original: dict[str, Any],
    budget: int = 300_000,
) -> dict[str, Any]:
    """Require readonly replay with original Unknown, process, source and budget."""
    before = producers(member)
    observed = cli.run(request(member, command, budget), "original-unknown-readonly-retry")
    if (
        observed["disposition"] != "unknown"
        or observed["original"] != original
        or producers(member) != before
    ):
        raise ValueError(
            "same original adoption ID repeated work, renewed evidence or fabricated completion"
        )
    return observed


def fixture(
    output: Path,
    workers: Processes,
    cli: Processes,
    source_sql: Path,
    source_cas: Path,
    profile: str,
    first: int,
    original_probe: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create real v4/Root2 sources, check occupied writers, then retain native waits."""
    members = [
        {"root": str(output / f"member-{member}"), "profile": profile, "member": member}
        for member in (first, first + 1)
    ]
    process = workers.start(
        {
            "action": "exercise",
            "source_sql": str(source_sql),
            "source_cas": str(source_cas),
            "first": members[0],
            "second": members[1],
            "hold": True,
        },
        "actual-original-v4-parents",
        original_probe,
    )
    ready = workers.ready(process)
    for member in members:
        cli.refuse(
            {"operation": "upgrade-root", "member": declaration(member)},
            "original-live-root-writer-refuses",
            "still active",
        )
    wait, _ = workers.wait(process, finish=True)
    if wait != 0:
        raise ValueError("original managed v4 writers did not close with their actual zero wait")
    for member in members:
        cli.run(
            {"operation": "upgrade-root", "member": declaration(member)},
            "actual-original-root-upgrade",
        )
    return members, ready


def complete(
    cli: Processes, workers: Processes, member: dict[str, Any], command: str
) -> dict[str, Any]:
    """Check new publication, unchanged business/legacy rows and normal reopen."""
    before, pins = business(member), legacy(member)
    observed = cli.run(request(member, command), "actual-original-parent-adoption")
    if observed["disposition"] != "published" or parent(member)[0] != 6:
        raise ValueError("actual parent adoption lacks its native new generation/publication")
    if business(member) != before or legacy(member) != pins:
        raise ValueError("actual parent adoption rewrote original business or legacy evidence")
    original_sources = observed["original"]["intent"]["sources"]
    kinds = {source["source"]["kind"] for source in original_sources}
    if not {"view", "snapshot-build"} <= kinds:
        raise ValueError("actual original parent adoption lost independent current responsibility")
    retained = producers(member)
    if (
        cli.run(request(member, command), "original-published-readonly-retry") != observed
        or producers(member) != retained
    ):
        raise ValueError("published original adoption replay changed its actual evidence")
    probe_member = {**member, "producer_journal": True}
    current = workers.run(
        {"action": "inspect", "member": probe_member}, "actual-adopted-parent-reopen"
    )
    if not current["actual_bytes"]:
        raise ValueError("actual adopted parent lacks independently verified original bytes")
    return {
        "observation": observed,
        "original_business_identical": True,
        "legacy_raw_identical": True,
        "new_current_sources": sorted(kinds),
        "reopened": current,
    }


def locked_fault(
    cli: Processes, workers: Processes, member: dict[str, Any], kind: str
) -> dict[str, Any]:
    """Inject SQL locks, expiry or native death after the original durable intent."""
    command = f"actual-original-adoption-{kind}"
    budget = 5_000 if kind == "expiry" else 300_000
    before, pins = business(member), legacy(member)
    root_path = Path(member["root"]) / "content/content.sqlite3"
    parent_path = Path(member["root"]) / "replica.sqlite3"
    lease_evidence: dict[str, Any] | None = None
    with (
        PayloadReadBarrier(payload_path(member)) as barrier,
        closing(sqlite3.connect(root_path, timeout=0.1)) as root_sql,
    ):
        child = cli.start(request(member, command, budget), f"actual-original-{kind}")
        original = wait_intent(child, member, command)
        save(cli.output / f"original-{kind}.json", original)
        deadline = time.monotonic() + 10
        while not barrier.signals:
            if child.poll() is not None:
                _, failure = cli.wait(child)
                raise ValueError(
                    f"original adoption exited before its actual CAS barrier: {failure}"
                )
            if time.monotonic() >= deadline:
                raise ValueError("actual original kernel lease-break observation expired")
            time.sleep(0.001)
        blocked_at = time.monotonic()
        if kind == "committed-killed":
            while time.monotonic() - blocked_at < 0.25:
                if child.poll() is not None or parent(member)[0] != 4:
                    raise ValueError("actual CAS barrier lost original pre-Commit work")
                time.sleep(0.01)
        stopped = stop_owned_child(child)
        rows = [row for row in producers(member) if row["intent"]["budget"]["pid"] == child.pid]
        if (
            stopped != signal.SIGSTOP
            or len(rows) != len(original["intent"]["sources"])
            or any(row["publication"] is not None for row in rows)
            or parent(member)[0] != 4
        ):
            raise ValueError("actual kernel barrier lacks complete original intents before Commit")
        lease_evidence = {
            "actual_kernel_signals": list(barrier.signals),
            "actual_stopped_signal": stopped,
            "actual_blocked_seconds": time.monotonic() - blocked_at,
            "original_file_mode": barrier.original_mode,
            "actual_source": str(barrier.path),
        }
        if kind == "killed":
            barrier.close()
            child.kill()
            wait, _ = cli.wait(child)
            if wait != -signal.SIGKILL:
                raise ValueError("original adoption lacks its actual killed wait result")
        elif kind == "expiry":
            barrier.close()
            original_budget = original["intent"]["budget"]
            deadline = time.monotonic() + budget / 1000 + 1
            while int(float(Path("/proc/uptime").read_text().split()[0]) * 1000) <= (
                original_budget["admitted_ms"] + original_budget["duration_ms"] + 20
            ):
                if time.monotonic() >= deadline:
                    raise ValueError("actual original kernel deadline did not elapse")
                time.sleep(0.02)
            os.kill(child.pid, signal.SIGCONT)
            wait, failure = cli.wait(child)
            if wait == 0 or "budget expired" not in failure.get("error", ""):
                raise ValueError(
                    f"actual original adoption deadline did not reject effects: {failure}"
                )
        elif kind == "parent-sql":
            with closing(sqlite3.connect(parent_path, timeout=0.1)) as parent_sql:
                parent_sql.execute("BEGIN IMMEDIATE")
                barrier.close()
                os.kill(child.pid, signal.SIGCONT)
                wait, failure = cli.wait(child)
                if wait == 0 or "locked" not in failure.get("error", ""):
                    raise ValueError(
                        f"actual original parent publication did not roll back: {failure}"
                    )
                parent_sql.rollback()
        elif kind == "committed-killed":
            root_sql.execute("BEGIN IMMEDIATE")
            barrier.close()
            if stat.S_IMODE(barrier.path.stat().st_mode) != barrier.original_mode:
                raise ValueError("actual kernel barrier changed original CAS file permissions")
            os.kill(child.pid, signal.SIGCONT)
            deadline = time.monotonic() + 10
            while True:
                if child.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError(
                        "actual new generation did not commit before its receipt fault"
                    )
                try:
                    if parent(member)[0] == 6:
                        break
                except sqlite3.OperationalError as error:
                    if error.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        raise
                time.sleep(0.001)
            child.kill()
            wait, _ = cli.wait(child)
            if wait != -signal.SIGKILL:
                raise ValueError("original committed adoption lacks its exact killed wait")
            root_sql.rollback()
        else:
            raise ValueError("unrecognized closed native fault kind")
    if business(member) != before or legacy(member) != pins:
        raise ValueError("actual failed adoption changed original business/legacy source")
    observed = unknown(cli, member, command, original, budget)
    if kind == "committed-killed":
        if parent(member)[0] != 6:
            raise ValueError("actual committed-but-Unknown adoption lost its exact new generation")
        cli.refuse(
            request(member, command + "-new-repair"),
            "already-committed-cannot-repeat",
            "already adopted",
        )
        reopened = workers.run(
            {"action": "inspect", "member": {**member, "producer_journal": True}},
            "actual-committed-unknown-reopen",
        )
        if not reopened["actual_bytes"]:
            raise ValueError(
                "actual committed-Unknown parent cannot verify its exact original bytes"
            )
        repair: dict[str, Any] | None = None
    else:
        if parent(member)[0] != 4:
            raise ValueError("actual interrupted original unexpectedly published a new generation")
        repair = complete(cli, workers, member, command + "-independent-repair")
        if parent(member)[1][HISTORY][0] != original:
            raise ValueError("actual distinct repair changed original Unknown history")
    return {
        "kind": kind,
        "native_wait": wait,
        "kernel_read_barrier": lease_evidence,
        "original": observed,
        "independent_repair": repair,
        "original_business_identical": True,
        "legacy_raw_identical": True,
    }


def run(
    output: Path,
    probe: Path,
    maintenance: Path,
    source_sql: Path,
    source_cas: Path,
    profile: str,
    old_parent_probe: Path | None = None,
    original_probe: Path | None = None,
) -> dict[str, Any]:
    """Exercise the actual product CLI and native faults with zero Nodes/devices."""
    output.mkdir(mode=0o700)
    command_output = output / "maintenance"
    command_output.mkdir(mode=0o700)
    workers = Processes(probe, output)
    cli = Processes(maintenance, command_output, ("--content-maintenance",))
    binaries = {
        str(path): digest(path)
        for path in (probe, maintenance, old_parent_probe or probe, original_probe or probe)
    }
    result: dict[str, Any] = {
        "schema": "roboguide.original-parent-adoption-process-check/v0.1",
        "passed": False,
        "nodes": 0,
        "devices": 0,
        "models": 0,
        "simulators": 0,
        "binaries": binaries,
        "historical_parent_binary_available": old_parent_probe is not None,
        "historical_original_binary_available": original_probe is not None,
    }
    try:
        members, ready = fixture(
            output, workers, cli, source_sql, source_cas, profile, 71, original_probe or probe
        )
        first, second = members
        parent_path = Path(first["root"]) / "replica.sqlite3"
        alias = parent_path.with_name("actual-original-hardlink.sqlite3")
        os.link(parent_path, alias)
        with alias.open("r+b") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            cli.refuse(
                request(first, "actual-alias-held"),
                "actual-parent-inode-holder-refused",
                "inode already owned",
            )
        alias.unlink()
        with closing(sqlite3.connect(parent_path)) as old:
            old.execute("PRAGMA journal_mode=WAL")
            old.execute("BEGIN")
            old.execute("SELECT * FROM replica_v1_meta").fetchall()
            cli.refuse(
                request(first, "actual-old-sql-held"),
                "actual-old-native-connection-refused",
                "not quiescent",
            )
            if parent(first)[1].get(HISTORY):
                raise ValueError("actual occupied original SQL admitted a new upgrade intent")
        result["successful"] = complete(cli, workers, first, "actual-original-positive-adoption")
        if old_parent_probe is not None:
            code, refused = workers.wait(
                workers.start(
                    {"action": "inspect", "member": {**first, "producer_journal": True}},
                    "actual-old-v5-parent-writer-fenced",
                    old_parent_probe,
                )
            )
            if code == 0 or "explicit original upgrade" not in refused.get("error", ""):
                raise ValueError("actual old v5 writer acquired adopted v6 authority")
            result["actual_old_parent_writer_refused"] = True
        key = ready["second"]["inputs"][0]
        content_file = (
            Path(second["root"])
            / "content/content-v1"
            / key["source_owner"].removeprefix("artifact-store:")
            / key["content_digest"].removeprefix("sha256:")
        )
        hidden = content_file.with_suffix(".original-held")
        content_file.rename(hidden)
        code, _ = cli.wait(
            cli.start(
                request(second, "actual-original-missing-content"),
                "actual-missing-original-content",
            )
        )
        if code == 0:
            raise ValueError("actual missing original CAS bytes published an adoption")
        missing_original = parent(second)[1][HISTORY][0]
        unknown(cli, second, "actual-original-missing-content", missing_original)
        hidden.rename(content_file)
        unknown(cli, second, "actual-original-missing-content", missing_original)
        result["missing_content_repair"] = complete(
            cli, workers, second, "actual-original-missing-content-repair"
        )
        if parent(second)[1][HISTORY][0] != missing_original:
            raise ValueError("actual restored-byte repair changed original missing-content Unknown")
        faults = []
        for first_member, kinds in (
            (79, ("killed", "expiry")),
            (89, ("parent-sql", "committed-killed")),
        ):
            pair, _ = fixture(
                output,
                workers,
                cli,
                source_sql,
                source_cas,
                profile,
                first_member,
                original_probe or probe,
            )
            for member, kind in zip(pair, kinds, strict=True):
                faults.append(locked_fault(cli, workers, member, kind))
        result["actual_faults"] = faults
        current = {**first, "producer_journal": True}
        built = workers.run(
            {
                "action": "operate",
                "member": current,
                "operation": {
                    "kind": "build",
                    "operation_id": "actual-normal-after-adoption",
                    "output": str(output / "actual-post-adoption.snapshot"),
                },
            },
            "actual-normal-snapshot-after-adoption",
        )
        result["normal_snapshot_after_adoption"] = built
        for key_name, expected in (
            (COMMIT, "mandatory original parent adoption Commit"),
            (HISTORY, "mandatory original parent adoption history"),
        ):
            with closing(sqlite3.connect(parent_path)) as sql, sql:
                raw = sql.execute(
                    "SELECT value FROM replica_v1_meta WHERE key=?", (key_name,)
                ).fetchone()[0]
                sql.execute("DELETE FROM replica_v1_meta WHERE key=?", (key_name,))
            workers.refuse(
                {"action": "inspect", "member": current},
                "missing-original-adoption-journal-refused",
                expected,
            )
            with closing(sqlite3.connect(parent_path)) as sql, sql:
                sql.execute("INSERT INTO replica_v1_meta VALUES (?,?)", (key_name, raw))
        if any(digest(Path(path)) != frozen for path, frozen in binaries.items()):
            raise ValueError(
                "actual maintained/native historical binaries changed during adoption acceptance"
            )
        result.update(
            passed=True,
            scope=("local adoption; no legacy pin retirement, quorum or physical continuity"),
            checker_sha256=digest(Path(__file__)),
        )
    finally:
        workers.cleanup()
        cli.cleanup()
        result["storage_processes"] = [record for _, record in workers.owned]
        result["maintenance_processes"] = [record for _, record in cli.owned]
        save(output / "result.json", result)
    return result
