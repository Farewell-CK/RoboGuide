"""Offline progress, source freshness, and truthful cancellation regressions."""

from __future__ import annotations

import json
import sys
import threading
import tomllib
import urllib.request
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from habitat_local_eaios import execution_progress  # noqa: E402
from habitat_local_eaios.adapter import HabitatLocalAdapter  # noqa: E402
from habitat_local_eaios.backend import (  # noqa: E402
    HabitatBackendConfig,
    HabitatMobilityBackend,
)
from habitat_local_eaios.execution_progress import (  # noqa: E402
    NavigationProgressPublisher,
    read_execution_progress,
)
from habitat_local_eaios.http_service import HabitatBridgeServer  # noqa: E402
from habitat_local_eaios.model import CanonicalMobilityInvocation  # noqa: E402
from habitat_local_eaios.store import ExecutionStore, StoredExecution  # noqa: E402


class Clock:
    """Advance sample time deterministically without sleeping."""

    def __init__(self) -> None:
        """Start at a valid monotonic time."""
        self.now = 1_000_000_000

    def __call__(self) -> int:
        """Read without changing time or sample identity."""
        return self.now

    def advance(self) -> None:
        """Advance exactly one configured producer sampling period."""
        self.now += 250_000_000


class NavigationEnvironment:
    """Existing positions plus an explicit action counter, with no RNG or pathfinder."""

    def __init__(self) -> None:
        """Create a ten-metre target distance and preserve action/observation separation."""
        self.base = [10.0, 0.0, 0.0]
        self.target = [0.0, 0.0, 0.0]
        self.reads = 0
        self.steps = 0
        self.resets = 0
        self.episode_over = False
        self.current_episode = SimpleNamespace(scene_id="scene")
        self.sim = SimpleNamespace(get_agent_data=self.get_agent_data)
        self.task = SimpleNamespace(
            pddl_problem=SimpleNamespace(
                get_entity=self.get_entity,
                get_ordered_entities_list=lambda: ["target"],
                sim_info=SimpleNamespace(get_entity_pos=self.get_entity_pos),
            )
        )

    def get_agent_data(self, agent_id: int) -> Any:
        """Read the existing base position, never advance physical work."""
        assert agent_id == 0
        self.reads += 1
        return SimpleNamespace(articulated_agent=SimpleNamespace(base_pos=self.base))

    def get_entity(self, destination: str) -> str | None:
        """Resolve only the supplied semantic destination."""
        return "target" if destination == "target" else None

    def get_entity_pos(self, entity: str) -> list[float]:
        """Return the stored goal position without evaluating predicates."""
        assert entity == "target"
        return self.target

    def reset(self) -> dict[str, object]:
        """Count only the backend's original reset."""
        self.resets += 1
        return {}

    def step(self, action: object) -> dict[str, object]:
        """Apply exactly one original action and never finish before explicit cancel."""
        assert isinstance(action, dict)
        self.steps += 1
        self.base[0] -= 0.5
        return {"agent_0_has_finished_oracle_nav": [0]}

    def get_metrics(self) -> dict[str, object]:
        """Retain the independent official outcome without deriving success from distance."""
        return {"pddl_success": False}


def _invocation(attempt: str | None = "attempt-1") -> CanonicalMobilityInvocation:
    """Build an invocation with a real attempt field, not a derived local handle."""
    return CanonicalMobilityInvocation(
        "mission",
        "task",
        "group",
        "role",
        "mobility.move@v1",
        "Reach the target",
        {"destination": "target"},
        ("slot",),
        attempt_id=attempt,
    )


def _row(tmp_path: Path, invocation: CanonicalMobilityInvocation) -> StoredExecution:
    """Create one running local execution with the same supplied attempt."""
    store = ExecutionStore(tmp_path / "store.sqlite3")
    row, _ = store.create_or_get(invocation)
    store.mark_running(row["execution_id"], "real backend started")
    stored = store.get(row["execution_id"])
    assert stored is not None
    return stored


def _sample(directory: Path) -> dict[str, Any]:
    """Read the local observation sidecar solely for independent test assertions."""
    document = json.loads((directory / "agent-0.json").read_bytes())
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


@pytest.mark.parametrize("attempt", [None, "x" * 257])
def test_no_attempt_never_invents_runtime_identity(tmp_path: Path, attempt: str | None) -> None:
    """Compatibility invocations without a bounded attempt remain unknown."""
    publisher = NavigationProgressPublisher(tmp_path, _invocation(attempt), 0)
    publisher.observe(object(), "nav_to_obj")
    assert not (tmp_path / "agent-0.json").exists()


def test_disabled_observer_does_not_read_or_create_files(tmp_path: Path) -> None:
    """Default-off observation never touches the environment, clock, or disk."""
    publisher = NavigationProgressPublisher(None, _invocation(), 0)
    publisher.observe(object(), "nav_to_obj")
    assert read_execution_progress(None, 0, None)["executions"] == []
    assert not list(tmp_path.iterdir())


def test_counter_measures_3d_improvement_and_never_counts_simulator_steps(tmp_path: Path) -> None:
    """Movement away/detours do not reduce the counter or masquerade as progress."""
    clock = Clock()
    environment = NavigationEnvironment()
    environment.base[:] = [0.0, 10.0, 0.0]
    publisher = NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock)
    publisher.observe(environment, "nav_to_obj")
    assert _sample(tmp_path)["sample"]["completed_units"] == 0
    environment.base[1] = 9.0
    clock.advance()
    publisher.observe(environment, "nav_to_obj")
    assert _sample(tmp_path)["sample"]["completed_units"] == 100
    environment.base[1] = 12.0
    clock.advance()
    publisher.observe(environment, "nav_to_obj")
    assert _sample(tmp_path)["sample"]["completed_units"] == 100
    assert environment.steps == environment.resets == 0


def test_wait_and_new_navigation_stage_rebaseline_units(tmp_path: Path) -> None:
    """Intentional wait has no progress deadline, and new work stages reset legally."""
    clock = Clock()
    publisher = NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock)
    environment = NavigationEnvironment()
    publisher.observe(environment, "nav_to_obj")
    clock.advance()
    environment.base[0] = 8.0
    publisher.observe(environment, "nav_to_obj")
    first = _sample(tmp_path)["sample"]
    assert first["completed_units"] == 200
    clock.advance()
    publisher.observe(environment, "wait")
    waiting = _sample(tmp_path)["sample"]
    assert waiting["stage_epoch"] > first["stage_epoch"]
    assert waiting["activity"] == "waiting"
    assert waiting["completed_units"] is None
    clock.advance()
    publisher.observe(environment, "nav_to_obj")
    resumed = _sample(tmp_path)["sample"]
    assert resumed["stage_epoch"] > waiting["stage_epoch"]
    assert resumed["completed_units"] == 0


def test_moving_target_starts_new_stage_instead_of_counting_target_motion(tmp_path: Path) -> None:
    """A moved physical entity cannot masquerade as this robot's navigation progress."""
    clock = Clock()
    environment = NavigationEnvironment()
    publisher = NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock)
    publisher.observe(environment, "nav_to_obj")
    first = _sample(tmp_path)["sample"]
    clock.advance()
    environment.target[0] = 5.0
    publisher.observe(environment, "nav_to_obj")
    moved = _sample(tmp_path)["sample"]
    assert moved["stage_epoch"] > first["stage_epoch"]
    assert moved["completed_units"] == 0


def test_sampling_and_disk_space_are_bounded(tmp_path: Path) -> None:
    """An unchanged clock performs one read/write regardless of loop frequency."""
    clock = Clock()
    environment = NavigationEnvironment()
    publisher = NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock)
    for _ in range(1000):
        publisher.observe(environment, "nav_to_obj")
    assert environment.reads == 1
    assert [item.name for item in tmp_path.iterdir()] == ["agent-0.json"]
    assert (tmp_path / "agent-0.json").stat().st_size <= 4096


def test_geometry_fault_and_recovery_are_unavailable_then_new_stage(tmp_path: Path) -> None:
    """Missing geometry never becomes zero distance or successful navigation."""
    clock = Clock()
    publisher = NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock)
    publisher.observe(object(), "nav_to_obj")
    unavailable = _sample(tmp_path)["sample"]
    assert unavailable["activity"] == "unavailable"
    assert unavailable["completed_units"] is None
    clock.advance()
    publisher.observe(NavigationEnvironment(), "nav_to_obj")
    measured = _sample(tmp_path)["sample"]
    assert measured["stage_epoch"] > unavailable["stage_epoch"]
    assert measured["completed_units"] == 0


@pytest.mark.parametrize("skill", ["pick", "place", "reset_arm", "unknown"])
def test_unsupported_skill_has_no_navigation_progress(tmp_path: Path, skill: str) -> None:
    """Broad robot skill exposure cannot fabricate operation progress or guard approval."""
    publisher = NavigationProgressPublisher(tmp_path, _invocation(), 0)
    publisher.observe(NavigationEnvironment(), skill)
    assert _sample(tmp_path)["sample"]["activity"] == "unavailable"


def test_source_freshness_is_not_renewed_by_http_reads(tmp_path: Path) -> None:
    """Repeated reader polls, future timestamps, and cross-attempt files remain fenced."""
    clock = Clock()
    row = _row(tmp_path, _invocation())
    publisher = NavigationProgressPublisher(tmp_path, row["invocation"], 0, clock_ns=clock)
    publisher.observe(NavigationEnvironment(), "nav_to_obj")
    assert read_execution_progress(tmp_path, 0, row, clock_ns=clock)["executions"]
    document = (tmp_path / "agent-0.json").read_bytes()
    for _ in range(8):
        clock.advance()
        read_execution_progress(tmp_path, 0, row, clock_ns=clock)
    assert read_execution_progress(tmp_path, 0, row, clock_ns=clock)["executions"] == []
    assert (tmp_path / "agent-0.json").read_bytes() == document
    clock.now = 0
    assert read_execution_progress(tmp_path, 0, row, clock_ns=clock)["executions"] == []
    clock.now = 1_000_000_000
    other = dict(row)
    other["invocation"] = replace(row["invocation"], attempt_id="attempt-2")
    assert (
        read_execution_progress(tmp_path, 0, cast(StoredExecution, other), clock_ns=clock)[
            "executions"
        ]
        == []
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "wrong"),
        ("agent_id", 1),
        ("agent_id", False),
        ("destination", "other"),
        ("observed_at_monotonic_ns", True),
        ("sample", []),
    ],
)
def test_bad_local_metadata_remains_unknown(tmp_path: Path, field: str, value: object) -> None:
    """Misbound or malformed sidecars cannot become fresh current-owner observations."""
    clock = Clock()
    row = _row(tmp_path, _invocation())
    NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock).observe(
        NavigationEnvironment(), "nav_to_obj"
    )
    document = _sample(tmp_path)
    document[field] = value
    (tmp_path / "agent-0.json").write_text(json.dumps(document))
    assert read_execution_progress(tmp_path, 0, row, clock_ns=clock)["executions"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_id", "local-handle"),
        ("operation", {}),
        ("stage_epoch", -1),
        ("stage_epoch", True),
        ("completed_units", -1),
        ("completed_units", 2**64),
        ("activity", "blocked"),
        ("extra", "not in generic schema"),
    ],
)
def test_bad_sample_remains_unknown(tmp_path: Path, field: str, value: object) -> None:
    """Strict local decoding preserves the closed generic State payload contract."""
    clock = Clock()
    row = _row(tmp_path, _invocation())
    NavigationProgressPublisher(tmp_path, _invocation(), 0, clock_ns=clock).observe(
        NavigationEnvironment(), "nav_to_obj"
    )
    document = _sample(tmp_path)
    document["sample"][field] = value
    (tmp_path / "agent-0.json").write_text(json.dumps(document))
    assert read_execution_progress(tmp_path, 0, row, clock_ns=clock)["executions"] == []


@pytest.mark.parametrize("raw", [b"{", b"[]", b"x" * 4097])
def test_bad_json_and_oversized_file_are_bounded(tmp_path: Path, raw: bytes) -> None:
    """Corrupt observations are independent of durable execution state and outcome."""
    row = _row(tmp_path, _invocation())
    (tmp_path / "agent-0.json").write_bytes(raw)
    assert read_execution_progress(tmp_path, 0, row)["executions"] == []
    assert row["state"] == "RUNNING"


def test_accepted_wait_and_terminal_do_not_require_or_reuse_position_file(tmp_path: Path) -> None:
    """Admission waiting and physical terminal facts remain distinct from navigation units."""
    store = ExecutionStore(tmp_path / "store.sqlite3")
    row, _ = store.create_or_get(_invocation())
    accepted = read_execution_progress(tmp_path, 0, row)["executions"]
    assert isinstance(accepted, list)
    assert accepted[0]["activity"] == "waiting"
    assert accepted[0]["completed_units"] is None
    row["state"] = "CANCELLED"
    assert read_execution_progress(tmp_path, 0, row)["executions"] == []


@pytest.mark.parametrize("mode", ["off", "on", "write-failure"])
def test_observer_preserves_actions_reset_cancel_and_official_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Actual backend loop always performs six actions, one reset, and true observed stop."""
    environment = NavigationEnvironment()
    config = HabitatBackendConfig(
        Path("not-loaded.yaml"),
        "episode",
        0,
        20,
        0,
        progress_directory=None if mode == "off" else tmp_path,
    )
    backend = HabitatMobilityBackend(config)
    backend._habitat_env = environment
    backend._episode = environment.current_episode
    backend._numpy = SimpleNamespace(float32=float, asarray=lambda value, **options: value)
    backend._agent_count = 2
    if mode == "write-failure":

        def fail_write(path: Path, value: str) -> None:
            """Inject disk failure at the optional observer, never at the physical backend."""
            del path, value
            raise OSError("progress filesystem unavailable")

        monkeypatch.setattr(execution_progress, "write_text_atomic", fail_write)
    outcome = backend.execute(_invocation(), lambda: environment.steps == 6, lambda detail: None)
    assert environment.steps == 6
    assert environment.resets == 1
    assert outcome.state == "CANCELLED"
    assert outcome.simulator_steps == 6
    assert outcome.final_position == (7.0, 0.0, 0.0)
    assert outcome.benchmark_task_achieved is False
    assert outcome.terminal_basis == "cancellation"


def test_production_http_route_returns_exact_attempt_not_local_handle(tmp_path: Path) -> None:
    """Serve the real facade with a read-only State observation and unchanged Cancel semantics."""
    environment = NavigationEnvironment()
    config = HabitatBackendConfig(Path("unused"), "episode", 0, 20, 0)
    backend = HabitatMobilityBackend(config)
    backend.initialize = lambda: None  # type: ignore[method-assign]
    adapter = HabitatLocalAdapter(
        ExecutionStore(tmp_path / "http.sqlite3"),
        lambda: backend,
        1,
        progress_directory=tmp_path,
    )
    server = HabitatBridgeServer(("127.0.0.1", 0), adapter)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = {"invocation": _invocation().as_dict()}
        response = adapter.accept(request)
        local_id = str(response["execution_id"])
        assert local_id != _invocation().attempt_id
        adapter._store.mark_running(local_id, "read-only HTTP test")
        NavigationProgressPublisher(tmp_path, _invocation(), 0).observe(environment, "nav_to_obj")
        url = f"http://127.0.0.1:{server.server_port}/v1/executions/progress"
        with urllib.request.urlopen(url, timeout=2) as reply:
            document = json.load(reply)
        assert document["executions"][0]["execution_id"] == "attempt-1"
        assert document["executions"][0]["operation"] == {
            "namespace": "mobility",
            "name": "move",
            "version": "v1",
        }
        cancelled = adapter.cancel({"execution_id": local_id})
        assert cancelled["state"] == "RUNNING"
        assert cancelled["accepted"] is True
        assert adapter.status({"execution_id": local_id})["state"] == "RUNNING"
        assert environment.steps == environment.resets == 0
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
        adapter.close()


@pytest.mark.parametrize("suffix", ["a", "b"])
def test_current_node_config_binds_progress_to_operation_owner(suffix: str) -> None:
    """The real B1 templates register one closed progress channel on their own Node."""
    path = ROOT.parents[1] / "scenarios/e1-shared-world-episode-51" / f"node-{suffix}.toml"
    config = tomllib.loads(path.read_text())
    exports = config["state_exports"]
    assert len(exports) == 1
    export = exports[0]
    assert export["owner"] == "habitat-local-eaios"
    assert export["object_id"] == config["node_id"]
    assert export["semantic"] == "reported"
    assert export["payload_schema"] == "roboguide.execution-progress/v0.1"
    assert export["step"]["operation"]["method"] == "GET"
    assert export["step"]["operation"]["path"] == "/v1/executions/progress"
