"""Offline checks for truthful recovery support, registration and read-only HTTP evidence."""

from __future__ import annotations

import json
import sys
import threading
import tomllib
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from habitat_local_eaios import __main__ as bridge_main  # noqa: E402
from habitat_local_eaios.adapter import HabitatLocalAdapter  # noqa: E402
from habitat_local_eaios.execution_recovery import execution_recovery_profile  # noqa: E402
from habitat_local_eaios.http_service import HabitatBridgeServer  # noqa: E402
from habitat_local_eaios.shared_world import NodeEndpoint, SharedWorldCoordinator  # noqa: E402
from habitat_local_eaios.store import ExecutionStore  # noqa: E402


class ForbiddenWorld:
    """Fail on any coordinator/world access, including health or reset queries."""

    def __getattr__(self, name: str) -> Any:
        """Make unexpected state, simulator, model and RNG accesses immediately observable."""
        raise AssertionError(f"recovery support must not query world dependency: {name}")


def _endpoint(tmp_path: Path) -> NodeEndpoint:
    """Create an endpoint without starting any worker or owning a physical world."""
    return NodeEndpoint(
        "arbitrary-node",
        3,
        ExecutionStore(tmp_path / "store.sqlite3"),
        cast(SharedWorldCoordinator, ForbiddenWorld()),
    )


@pytest.mark.parametrize("shared_world", [False, True])
def test_profile_has_exact_operation_identity_and_never_claims_retry(shared_world: bool) -> None:
    """Scope varies with deployment; neither reset-based backend supports retained-world retry."""
    profile = execution_recovery_profile(shared_world=shared_world)
    operations = cast(list[dict[str, Any]], profile["operations"])
    assert {entry["operation"]["name"] for entry in operations} == {"move", "navigate"}
    assert all(entry["operation"]["namespace"] == "mobility" for entry in operations)
    assert all(entry["operation"]["version"] == "v1" for entry in operations)
    assert {entry["stop_scope"] for entry in operations} == {
        "execution-group" if shared_world else "execution"
    }
    assert {entry["continuation"] for entry in operations} == {"unsupported"}
    operations[0]["continuation"] = "repeat-after-stop"
    assert execution_recovery_profile(shared_world=shared_world) != profile


def test_registration_matches_shared_endpoint_readonly_support(tmp_path: Path) -> None:
    """Both actual Node configs declare the same exact owner/op facts as the HTTP adapter."""
    endpoint = _endpoint(tmp_path)
    before = endpoint.store().all_executions()
    observed = endpoint.recovery_support()
    for node in ("node-a", "node-b"):
        path = ROOT.parents[1] / f"scenarios/e1-shared-world-episode-51/{node}.toml"
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        owner = next(
            system for system in config["local_systems"] if system["id"] == "habitat-local-eaios"
        )
        assert json.loads(owner["metadata"]["roboguide.execution-recovery"]) == observed
        assert {
            f"{entry['operation']['namespace']}.{entry['operation']['name']}@{entry['operation']['version']}"
            for entry in cast(list[dict[str, Any]], observed["operations"])
        } == {
            operation["operation"]
            for operation in config["operations"]
            if operation["owner"] == owner["id"]
        }
    assert endpoint.store().all_executions() == before


def test_standalone_support_does_not_need_worker_or_backend() -> None:
    """Reading deployment facts cannot construct a simulator, call a model or change lifecycle."""
    adapter = HabitatLocalAdapter.__new__(HabitatLocalAdapter)
    assert adapter.recovery_support() == execution_recovery_profile(shared_world=False)


def test_opted_in_continuation_still_declares_group_stop_only() -> None:
    """Retained-world support cannot turn coupled cancellation into isolated Role permission."""
    profile = execution_recovery_profile(shared_world=True, retain_stopped_session=True)
    operations = cast(list[dict[str, Any]], profile["operations"])
    assert {entry["stop_scope"] for entry in operations} == {"execution-group"}
    assert {entry["continuation"] for entry in operations} == {"repeat-after-stop"}
    standalone = execution_recovery_profile(shared_world=False, retain_stopped_session=True)
    assert standalone == execution_recovery_profile(shared_world=False)


def test_cli_continuation_is_default_off_and_rejects_standalone_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opt-in cannot silently apply to a reset-based backend or change existing launch defaults."""
    arguments = [
        "bridge",
        "--state-db",
        str(tmp_path / "state.sqlite3"),
        "--habitat-config",
        str(tmp_path / "config.yaml"),
        "--episode-id",
        "generic",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    assert bridge_main._arguments().retain_stopped_session is False
    monkeypatch.setattr(sys, "argv", [*arguments, "--retain-stopped-session"])
    with pytest.raises(SystemExit, match="shared EMOS Stage2"):
        bridge_main.main()
    assert not (tmp_path / "state.sqlite3").exists()


def test_http_support_read_does_not_observe_or_mutate_world(tmp_path: Path) -> None:
    """Return declaration facts without health queries, execution or physical side effects."""
    endpoint = _endpoint(tmp_path)
    server = HabitatBridgeServer(("127.0.0.1", 0), endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/v1/executions/recovery-support", timeout=2
        ) as response:
            assert response.status == 200
            assert json.load(response) == endpoint.recovery_support()
        assert endpoint.store().all_executions() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
