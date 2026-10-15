"""Adversarial framing, file ownership and interrupted-job checks without device substitutes."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest
from roboguide_spatial_provider.__main__ import observe, run
from roboguide_spatial_provider.contracts import Job, canonical, digest
from roboguide_spatial_provider.geometry import code_digest
from roboguide_spatial_provider.http_service import SpatialService
from roboguide_spatial_provider.node_profile import node_config
from roboguide_spatial_provider.storage import point_count, read_source, save_json


def service_configuration() -> dict[str, Any]:
    """Describe bounded file metadata only; unavailable source hashes cannot prove computation."""
    return {
        "schema_version": "roboguide.spatial-local-service/v0.1",
        "max_jobs": 4,
        "max_active": 1,
        "profiles": [
            {
                "profile_id": "opaque-slot",
                "map_id": "file-map",
                "revision_id": "original",
                "spatial_anchor_id": "source-coordinate-frame",
                "job": request(),
            }
        ],
    }


def service_request(service: SpatialService, publish: bool) -> dict[str, Any]:
    """Supply canonical software invocation framing without asserting any execution outcome."""
    profile = service.profiles["opaque-slot"]
    return {
        "operation": "publish-map" if publish else "build-map",
        "attempt_id": "original-attempt",
        "invocation": {
            "operation": "spatial.map.publish@v0" if publish else "spatial.map.build@v0",
            "parameters": {
                "map_id": profile["map_id"],
                "revision_id": profile["revision_id"],
                "spatial_anchor_id": profile["spatial_anchor_id"],
                "artifact_slot": profile["profile_id"],
                "artifact_operation": "publish" if publish else "prepare-output",
            },
        },
        "artifact_path": str(service._job_directory(profile) / "map.bundle"),
    }


def test_local_service_single_writer_and_config_change_are_observation_only(tmp_path: Path) -> None:
    """A second owner/config cannot retarget old storage or gain new execution through readonly."""
    configuration = service_configuration()
    service = SpatialService(tmp_path / "backend", tmp_path, tmp_path / "cache", configuration)
    identity = service.identity.copy()
    try:
        with pytest.raises(BlockingIOError):
            SpatialService(tmp_path / "backend", tmp_path, tmp_path / "cache", configuration)
    finally:
        service.close()
    changed = copy.deepcopy(configuration)
    changed["profiles"][0]["job"]["policy"]["seed"] += 1
    with pytest.raises(ValueError, match="observation-only"):
        SpatialService(tmp_path / "backend", tmp_path, tmp_path / "cache", changed)
    observer = SpatialService(tmp_path / "backend", tmp_path, tmp_path / "cache", changed, True)
    try:
        assert observer.identity == identity
        with pytest.raises(ValueError, match="readonly"):
            observer.submit(service_request(observer, False))
        assert observer.records == {}
    finally:
        observer.close()
    with pytest.raises(ValueError, match="roots changed"):
        SpatialService(tmp_path / "backend", tmp_path, tmp_path / "different-cache", changed, True)


@pytest.mark.parametrize("mutation", ["operation", "revision", "path", "foreign-slot"])
def test_local_service_semantic_and_path_mismatch_cannot_dispatch(
    tmp_path: Path, mutation: str
) -> None:
    """Wrong operations, selectors, paths or slot injection leave no original intent."""
    service = SpatialService(
        tmp_path / "backend", tmp_path, tmp_path / "cache", service_configuration()
    )
    try:
        body = service_request(service, False)
        if mutation == "operation":
            body["invocation"]["operation"] = "mobility.navigate@v1"
        elif mutation == "revision":
            body["invocation"]["parameters"]["revision_id"] = "another"
        elif mutation == "path":
            body["artifact_path"] = str(tmp_path / "outside.bundle")
        else:
            body["artifact_paths"] = {"opaque-slot": body.pop("artifact_path"), "foreign": "/other"}
        with pytest.raises(ValueError):
            service.submit(body)
        assert service.records == {} and list(service.records_root.iterdir()) == []
    finally:
        service.close()


def test_failed_publication_retry_never_replaces_original_attempt(tmp_path: Path) -> None:
    """Missing original computation remains a real failure and retries cannot revise admission."""
    service = SpatialService(
        tmp_path / "backend", tmp_path, tmp_path / "cache", service_configuration()
    )
    try:
        body = service_request(service, True)
        original = service.submit(body)
        assert original["state"] == "FAILED"
        assert service.submit(body) == original
        body["artifact_paths"] = {"opaque-slot": body.pop("artifact_path")}
        assert service.submit(body) == original
        body["invocation"]["objective"] = "replace original request"
        with pytest.raises(ValueError, match="conflicts"):
            service.submit(body)
        assert len(service.records) == 1
        assert service._processes == {}
    finally:
        service.close()


def test_node_profile_uses_configured_selectors_and_arbitrary_node_identity(tmp_path: Path) -> None:
    """Multiple semantic slots share fixed canonical workflows without device-specific mappings."""
    configuration = service_configuration()
    another = copy.deepcopy(configuration["profiles"][0])
    another.update(profile_id="another-slot", map_id="another-map", revision_id="other")
    another["job"]["job_id"] = "other"
    configuration["profiles"].append(another)
    parsed = tomllib.loads(
        node_config(
            configuration,
            node_id="deployment-selected-process-identity",
            server_endpoint="http://127.0.0.1:50051",
            artifact_endpoint="http://127.0.0.1:8090",
            state_root=tmp_path / "state",
            cache_root=tmp_path / "cache",
            socket_path=tmp_path / "backend/local.sock",
        )
    )
    assert parsed["node_id"] == "deployment-selected-process-identity"
    assert {binding["id"] for binding in parsed["artifacts"]["output_bindings"]} == {
        "opaque-slot",
        "another-slot",
    }
    assert len({binding["source_path"] for binding in parsed["artifacts"]["output_bindings"]}) == 2
    assert parsed["connections"][0]["endpoint"] == "unix://" + str(tmp_path / "backend/local.sock")
    assert {operation["operation"] for operation in parsed["operations"]} == {
        "spatial.map.build@v0",
        "spatial.map.publish@v0",
    }


def policy() -> dict[str, Any]:
    """Declare explicit small software-test budgets; these are not device performance evidence."""
    return {
        "voxel_size": 0.05,
        "normal_radius": 0.1,
        "feature_radius": 0.25,
        "ransac_distance": 0.1,
        "icp_distance": 0.05,
        "min_fitness": 0.5,
        "max_rmse": 0.04,
        "min_shape_ratio": 0.00001,
        "max_cycle_translation": 0.1,
        "max_cycle_angle": 0.1,
        "tile_size": 1.0,
        "ransac_trials": 1000,
        "ransac_repeats": 2,
        "icp_iterations": 20,
        "seed": 7,
        "max_input_bytes": 1048576,
        "max_points": 10000,
        "max_total_points": 20000,
        "max_downsample_points": 10000,
        "max_output_bytes": 1048576,
        "max_tiles": 64,
        "timeout_s": 10,
        "memory_mb": 4096,
        "threads": 2,
    }


def request() -> dict[str, Any]:
    """Provide framing metadata; actual geometry uses the separate native runner."""
    return {
        "schema_version": "roboguide.spatial-file-job/v0.1",
        "job_id": "original",
        "frame_id": "input-frame",
        "inputs": [
            {
                "source_id": "file",
                "frame_id": "input-frame",
                "path": "source.pcd",
                "content_digest": "sha256:" + "a" * 64,
                "units": "m",
                "format": "pcd",
            }
        ],
        "policy": policy(),
    }


def test_duplicate_source_frames_cannot_be_counted_as_independent_geometry() -> None:
    """An alias cannot create another input or an unstated output coordinate frame."""
    value = request()
    alias = copy.deepcopy(value["inputs"][0])
    alias["source_id"] = "another-name"
    value["inputs"].append(alias)
    with pytest.raises(ValueError, match="duplicate"):
        Job.parse(value)
    value = request()
    value["frame_id"] = "invented-global-frame"
    with pytest.raises(ValueError, match="anchor"):
        Job.parse(value)
    value = request()
    value["policy"]["max_points"] = True
    with pytest.raises(ValueError, match="integer"):
        Job.parse(value)


def test_pcd_allocation_envelope_and_source_symlinks_fail_before_native_decode(
    tmp_path: Path,
) -> None:
    """Small malicious headers cannot allocate huge arrays or escape source roots."""
    header = (
        b"FIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
        b"WIDTH 1000000000\nHEIGHT 1000000000\nPOINTS 3\nDATA ascii\n"
        b"0 0 0\n1 0 0\n0 1 0\n"
    )
    with pytest.raises(ValueError, match="width/height"):
        point_count(header, "pcd", 1000)
    (tmp_path / "source").write_bytes(b"actual private file")
    (tmp_path / "alias").symlink_to(tmp_path / "source")
    with pytest.raises(OSError):
        read_source(tmp_path, "alias", 100)
    (tmp_path / "directory-alias").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        read_source(tmp_path, "directory-alias/source", 100)
    with pytest.raises(ValueError):
        read_source(tmp_path, "../source", 100)


@pytest.mark.parametrize("field,value", [("units", []), ("format", {}), ("units", None)])
def test_input_enum_shapes_fail_before_files_are_opened(field: str, value: Any) -> None:
    """Malformed JSON cannot bypass the typed unit/format boundary through an unhashable value."""
    original = request()
    original["inputs"][0][field] = value
    with pytest.raises(ValueError, match="units/format"):
        Job.parse(original)


def test_interrupted_identity_returns_original_unknown_without_new_computation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No source bytes, newer code or explicit retry renews an original interrupted job deadline."""
    value = request()
    directory = tmp_path / "store/original"
    directory.mkdir(parents=True)
    save_json(directory / "request.json", value)
    save_json(
        directory / "intent.json",
        {
            "job_id": "original",
            "request_digest": digest(canonical(value)),
            "code_digest": code_digest(),
            "admitted_at_ms": 1,
            "deadline_ms": 10001,
        },
    )

    def forbidden(*args: Any, **kwargs: Any) -> None:
        """Fail if retry attempts native work after the interrupted original intent."""
        raise AssertionError("interrupted job launched another worker")

    monkeypatch.setattr("roboguide_spatial_provider.__main__.subprocess.Popen", forbidden)
    observed = run(value, tmp_path, tmp_path / "store")
    assert observed["status"] == "unknown" and observed["deadline_ms"] == 10001
    assert observed["admitted_at_ms"] == 1
    changed = copy.deepcopy(value)
    changed["policy"]["timeout_s"] = 20
    with pytest.raises(ValueError, match="conflicts"):
        run(changed, tmp_path, tmp_path / "store")
    assert not (directory / "started.json").exists()


def test_source_digest_failure_is_durable_and_never_promoted_by_retry(tmp_path: Path) -> None:
    """Missing immutable source proof rejects before native work without a retry reset."""
    value = request()
    (tmp_path / "source.pcd").write_bytes(b"actual bytes with another digest")
    first = run(value, tmp_path, tmp_path / "store")
    assert first["status"] == "rejected_before_compute"
    assert "digest" in first["reason"]
    assert run(value, tmp_path, tmp_path / "store") == first
    assert not (tmp_path / "store/original/started.json").exists()


@pytest.mark.parametrize("status", ["completed", "completed_late", "cancelled", "unavailable"])
def test_unproven_terminal_strings_never_become_computed_or_stopped_evidence(
    tmp_path: Path,
    status: str,
) -> None:
    """A partial/corrupt outcome sidecar cannot manufacture byte ownership or CPU stopping."""
    identity = digest(canonical(request()))
    save_json(
        tmp_path / "intent.json",
        {
            "job_id": "original",
            "request_digest": identity,
            "admitted_at_ms": 1,
            "deadline_ms": 10001,
        },
    )
    save_json(
        tmp_path / "result.json",
        {
            "status": status,
            "request_digest": identity,
            "artifact": {"content_digest": "invented"},
            "manifest": {"current_node_localization": "invented"},
        },
    )
    value = observe(tmp_path)
    assert value["status"] == "unknown"
    assert "artifact" not in value and "manifest" not in value and "stop_assurance" not in value
