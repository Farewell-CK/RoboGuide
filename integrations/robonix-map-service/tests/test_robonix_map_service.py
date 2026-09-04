"""Offline contract and filesystem tests for the Robonix map integration."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest


def _load_adapter_module() -> ModuleType:
    """Load the deployment script without requiring an invalid hyphenated Python package name."""
    path = Path("integrations/robonix-map-service/robonix-map-service.py")
    spec = importlib.util.spec_from_file_location("roboguide_robonix_map_service", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Robonix map adapter module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = _load_adapter_module()


def _create_map(directory: Path, marker: str) -> None:
    """Create the minimum valid Robonix map layout with an integrity-checkable database."""
    directory.mkdir(parents=True)
    with sqlite3.connect(directory / "rtabmap.db") as connection:
        connection.execute("CREATE TABLE map_marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO map_marker(value) VALUES (?)", (marker,))
    (directory / "occupancy.pgm").write_bytes(f"P2\n1 1\n255\n{len(marker)}\n".encode())
    (directory / "occupancy.yaml").write_text("image: occupancy.pgm\n", encoding="utf-8")
    (directory / "meta.yaml").write_text(f"marker: {marker}\n", encoding="utf-8")


def _archive_map(source: Path, destination: Path) -> None:
    """Create one deterministic-enough test archive containing only regular map files."""
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(source.iterdir()):
            archive.add(path, arcname=path.name)


def _adapter(tmp_path: Path, ros_command: tuple[str, ...] = ()) -> Any:
    """Construct one adapter whose filesystem authority is confined to a temporary directory."""
    config = ADAPTER.AdapterConfig(
        map_root=tmp_path / "maps",
        artifact_root=tmp_path / "artifacts",
        state_db=tmp_path / "state" / "adapter.sqlite3",
        robonix_endpoint="http://127.0.0.1:1",
        request_timeout_s=0.05,
        max_archive_bytes=16 * 1024 * 1024,
        ros_service_list_command=ros_command,
        ros_discovery_timeout_s=0.2,
    )
    return ADAPTER.LocalAdapter(config)


def _invocation(map_id: str) -> dict[str, Any]:
    """Build the canonical identity fields required by one local import invocation."""
    return {
        "mission_id": "mission-test",
        "task_id": "task-import",
        "group_id": "group-test",
        "role_id": "role-import",
        "capability_contract": "spatial.map.import@v0",
        "parameters": {"map_id": map_id},
    }


def test_identical_import_is_idempotent_but_different_digest_conflicts(tmp_path: Path) -> None:
    """A local map name may be reused only when durable provenance proves identical bytes."""
    adapter = _adapter(tmp_path)
    try:
        source = tmp_path / "source-a"
        _create_map(source, "a")
        artifact = tmp_path / "artifacts" / "map-a.tar.gz"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        _archive_map(source, artifact)

        first = cast(str, adapter._import_map(artifact, _invocation("map-a")))
        repeated = cast(str, adapter._import_map(artifact, _invocation("map-a")))
        assert first == "imported map bundle as local map map-a"
        assert "already contains artifact sha256:" in repeated

        other_source = tmp_path / "source-b"
        _create_map(other_source, "different")
        other_artifact = tmp_path / "artifacts" / "map-b.tar.gz"
        _archive_map(other_source, other_artifact)
        with pytest.raises(ADAPTER.AdapterError, match="different artifact digest"):
            adapter._import_map(other_artifact, _invocation("map-a"))
    finally:
        adapter.close()


def test_existing_map_without_adapter_provenance_fails_closed(tmp_path: Path) -> None:
    """A same-named vendor map is not silently claimed as a RoboGuide import."""
    adapter = _adapter(tmp_path)
    try:
        _create_map(tmp_path / "maps" / "map-a", "manual")
        source = tmp_path / "source"
        _create_map(source, "manual")
        artifact = tmp_path / "artifacts" / "map.tar.gz"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        _archive_map(source, artifact)
        with pytest.raises(ADAPTER.AdapterError, match="no adapter provenance"):
            adapter._import_map(artifact, _invocation("map-a"))
    finally:
        adapter.close()


def test_archive_path_traversal_is_rejected(tmp_path: Path) -> None:
    """An imported tar member cannot escape the adapter-owned staging root."""
    adapter = _adapter(tmp_path)
    try:
        artifact = tmp_path / "artifacts" / "unsafe.tar.gz"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        payload = tmp_path / "payload"
        payload.write_text("unsafe", encoding="utf-8")
        with tarfile.open(artifact, "w:gz") as archive:
            archive.add(payload, arcname="../escape")
        with pytest.raises(ADAPTER.AdapterError, match="unsafe path"):
            adapter._archive_members(artifact)
    finally:
        adapter.close()


def test_health_reports_offline_when_robonix_is_unreachable(tmp_path: Path) -> None:
    """Process health must not claim the local Robonix WebUI is reachable after a probe failure."""
    adapter = _adapter(tmp_path)
    try:
        assert adapter.health()["state"] == "OFFLINE"
    finally:
        adapter.close()


def test_readiness_requires_exact_discovered_ros_services(tmp_path: Path) -> None:
    """Mapping and localization readiness follow exact service discovery independently."""
    adapter = _adapter(
        tmp_path,
        (
            sys.executable,
            "-c",
            "print('/rtabmap/set_mode_mapping')",
        ),
    )
    try:
        capabilities = cast(dict[str, dict[str, str]], adapter.readiness()["capabilities"])
        assert capabilities["spatial.map.build@v0"]["state"] == "READY"
        assert capabilities["spatial.localization.verify@v0"]["state"] == "UNAVAILABLE"
        assert capabilities["spatial.map.publish@v0"]["state"] == "READY"
        assert capabilities["spatial.map.import@v0"]["state"] == "READY"
    finally:
        adapter.close()


def test_readiness_fails_ros_capabilities_closed_when_discovery_fails(tmp_path: Path) -> None:
    """A failed ROS discovery command cannot leave ROS-dependent contracts ready."""
    adapter = _adapter(tmp_path, (sys.executable, "-c", "raise SystemExit(7)"))
    try:
        capabilities = cast(dict[str, dict[str, str]], adapter.readiness()["capabilities"])
        assert capabilities["spatial.map.build@v0"]["state"] == "UNAVAILABLE"
        assert capabilities["spatial.localization.verify@v0"]["state"] == "UNAVAILABLE"
        assert "exited with 7" in capabilities["spatial.map.build@v0"]["detail"]
    finally:
        adapter.close()


def test_readiness_fails_ros_capabilities_closed_when_discovery_times_out(
    tmp_path: Path,
) -> None:
    """A bounded discovery timeout leaves all ROS-dependent contracts unavailable."""
    adapter = _adapter(
        tmp_path,
        (sys.executable, "-c", "import time; time.sleep(2)"),
    )
    try:
        capabilities = cast(dict[str, dict[str, str]], adapter.readiness()["capabilities"])
        assert capabilities["spatial.map.build@v0"]["state"] == "UNAVAILABLE"
        assert capabilities["spatial.localization.verify@v0"]["state"] == "UNAVAILABLE"
        assert "timed out" in capabilities["spatial.map.build@v0"]["detail"]
    finally:
        adapter.close()


def test_readiness_applies_storage_dependencies_per_exact_contract(tmp_path: Path) -> None:
    """Losing map storage fences build/import/verify without hiding artifact publication."""
    adapter = _adapter(
        tmp_path,
        (
            sys.executable,
            "-c",
            "print('/rtabmap/set_mode_mapping\\n/rtabmap/set_mode_localization')",
        ),
    )
    try:
        adapter._config.map_root.rename(tmp_path / "detached-maps")
        capabilities = cast(dict[str, dict[str, str]], adapter.readiness()["capabilities"])
        assert capabilities["spatial.map.build@v0"]["state"] == "UNAVAILABLE"
        assert capabilities["spatial.map.publish@v0"]["state"] == "READY"
        assert capabilities["spatial.map.import@v0"]["state"] == "UNAVAILABLE"
        assert capabilities["spatial.localization.verify@v0"]["state"] == "UNAVAILABLE"
        assert "map storage is not accessible" in capabilities["spatial.map.build@v0"]["detail"]
    finally:
        adapter.close()
