"""Verify actual planar file geometry, independent SE(2) coordinates and occupancy conflicts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from check_spatial_provider_files import execute, job


def policy() -> dict[str, Any]:
    """Declare the complete bounded planar conformance profile independently of coordinate truth."""
    return {
        "resolution_meters": 0.2,
        "yaw_min": -0.6,
        "yaw_max": 0.6,
        "yaw_step": 0.1,
        "translation_bound_m": 0.2,
        "translation_step_m": 0.1,
        "max_pair_distance_m": 0.015,
        "min_overlap": 0.95,
        "max_rmse_m": 0.0001,
        "min_shape_ratio": 0.00001,
        "ambiguity_translation_m": 0.05,
        "ambiguity_yaw_rad": 0.05,
        "score_margin": 0.0,
        "max_cycle_translation_m": 0.02,
        "max_cycle_yaw_rad": 0.02,
        "free_coverage_margin_m": 0.0,
        "free_max": 20,
        "occupied_min": 80,
        "max_hypotheses": 1000,
        "max_refinements": 32,
        "icp_iterations": 50,
        "pose_graph_evaluations": 100,
        "max_registration_points": 2048,
        "max_occupied_points": 10000,
        "max_input_bytes": 16 * 1024**2,
        "max_cells": 100000,
        "max_total_cells": 500000,
        "max_output_bytes": 16 * 1024**2,
        "tile_cells": 16,
        "max_tiles": 256,
        "timeout_s": 60,
        "memory_mb": 8192,
        "threads": 2,
    }


def source(path: Path, frame: str, units: str = "m") -> dict[str, Any]:
    """Bind actual grid bytes with their explicit planar frame and unit declaration."""
    return {
        "source_id": path.stem,
        "path": path.name,
        "content_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "frame_id": frame,
        "units": units,
        "format": "occupancy-json",
    }


def independent_error(
    actual: list[list[float]], original_to_derived: list[list[float]]
) -> dict[str, float]:
    """Check the recovered inverse using stdlib arithmetic outside the registration process."""
    rotation = [[original_to_derived[column][row] for column in range(2)] for row in range(2)]
    translation = [
        -sum(rotation[row][column] * original_to_derived[column][2] for column in range(2))
        for row in range(2)
    ]
    distance = math.hypot(*(actual[row][2] - translation[row] for row in range(2)))
    true_yaw = math.atan2(rotation[1][0], rotation[0][0])
    estimated_yaw = math.atan2(actual[1][0], actual[0][0])
    yaw = abs(math.atan2(math.sin(estimated_yaw - true_yaw), math.cos(estimated_yaw - true_yaw)))
    if distance > 1e-6 or yaw > 1e-6:
        raise ValueError(f"independent SE(2) coordinate error exceeds bound: {distance}/{yaw}")
    return {"translation_error_m": distance, "rotation_error_rad": yaw}


def cells(path: Path) -> dict[tuple[int, int], int]:
    """Independently verify every actual tile hash and retain its declared observed cell masks."""
    result = {}
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for tile in manifest["tiles"]:
            content = archive.read(tile["path"])
            assert "sha256:" + hashlib.sha256(content).hexdigest() == tile["content_digest"]
            for x, y, mask in json.loads(content)["cells"]:
                assert (x, y) not in result and mask in (1, 2, 3)
                result[x, y] = mask
        assert len(result) == manifest["observed_cell_count"]
    return result


def run(python: Path, original: Path, original_sha256: str, output: Path) -> dict[str, Any]:
    """Project actual points, then check independent coordinates and adverse free observations."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    if hashlib.sha256(original.read_bytes()).hexdigest() != original_sha256:
        raise ValueError("original point-cloud source bytes differ")
    inputs = output / "inputs"
    inputs.mkdir()
    prep = r"""import json,math,sys
from pathlib import Path
import open3d as o,numpy as np
points=np.asarray(o.io.read_point_cloud(sys.argv[1]).points)[:,[0,2]]
r=0.2;minimum=np.floor(points.min(axis=0)/r)*r
indices=np.floor((points-minimum)/r).astype(int)
width,height=(indices.max(axis=0)+1).tolist()
values=np.full((height,width),-1,dtype=int)
values[indices[:,1],indices[:,0]]=100
base={"schema_version":"roboguide.occupancy-grid/v0.1","width":width,"height":height,
      "resolution":r,"origin":[*minimum.tolist(),0.0],"cells":values.reshape(-1).tolist()}
root=Path(sys.argv[2]);(root/"original.json").write_text(json.dumps(base))
truth=[]
profiles=(("derived-cm",0.2,[0.31,-0.17],100),("third",-0.3,[-0.17,0.21],1))
for label,theta,translation,units in profiles:
 c,s=math.cos(theta),math.sin(theta);rotation=np.array([[c,-s],[s,c]])
 t=np.eye(3);t[:2,:2]=rotation;t[:2,2]=translation;truth.append(t.tolist())
 shifted=rotation@minimum+translation
 derived={**base,"resolution":r*units,"origin":[*(shifted*units).tolist(),theta]}
 (root/(label+".json")).write_text(json.dumps(derived))
occupied=np.argwhere(values==100)
row,column=occupied[np.argmin(np.linalg.norm(occupied-occupied.mean(axis=0),axis=1))]
adverse=values.copy();adverse[row,column]=0
(root/"adversarial-free.json").write_text(json.dumps({**base,"cells":adverse.reshape(-1).tolist()}))
warped=np.full((height,width*3),-1,dtype=int)
for row,column in occupied:warped[row,column*(row%3+1)]=100
(root/"nonrigid.json").write_text(json.dumps({**base,"width":width*3,
 "cells":warped.reshape(-1).tolist()}))
print(json.dumps({"original_to_derived":truth,"original_points":len(points),
 "projected_occupied_cells":len(occupied),"projection":"actual-x-z-coordinates-only",
 "positive_free_cells":0,"adversarial_free_is_device_evidence":False}))
"""
    truth = execute(
        python, ["-c", prep, str(original), str(inputs)], output / "projection-provenance.json"
    )
    request: dict[str, Any] = {
        "schema_version": "roboguide.spatial-occupancy-job/v0.1",
        "job_id": uuid.uuid4().hex,
        "frame_id": "actual-file-projection",
        "policy": policy(),
        "inputs": [
            source(inputs / "original.json", "actual-file-projection"),
            source(inputs / "derived-cm.json", "derived-cm-frame", "cm"),
            source(inputs / "third.json", "derived-third-frame"),
        ],
    }
    result = job(python, output, inputs, request, "planar-loop")
    assert result["status"] == "completed", result
    manifest = result["manifest"]
    assert manifest["geometry"] == "SE(2)" and len(manifest["registration"]) == 3
    errors = [
        independent_error(manifest["transformations_to_anchor"][index + 1], expected)
        for index, expected in enumerate(truth["original_to_derived"])
    ]
    bundle = output / "jobs" / request["job_id"] / "map.bundle"
    original_bytes = bundle.read_bytes()
    base_cells = cells(bundle)
    assert all(value == 2 for value in base_cells.values())
    assert (10_000, 10_000) not in base_cells
    shutil.copyfile(bundle, inputs / "parent.bundle")
    incremental_request = {
        **request,
        "job_id": uuid.uuid4().hex,
        "inputs": [source(inputs / "third.json", "derived-third-frame")],
        "parent": {
            "path": "parent.bundle",
            "content_digest": result["artifact"]["content_digest"],
            "revision_id": request["job_id"],
            "frame_id": request["frame_id"],
        },
    }
    incremental = job(python, output, inputs, incremental_request, "planar-incremental")
    assert incremental["status"] == "completed", incremental
    assert bundle.read_bytes() == original_bytes
    adverse_request = {
        **request,
        "job_id": uuid.uuid4().hex,
        "policy": {**policy(), "min_overlap": 0.9},
        "inputs": [
            request["inputs"][0],
            source(inputs / "adversarial-free.json", "adversarial-frame"),
        ],
    }
    adverse = job(python, output, inputs, adverse_request, "conflicting-free")
    assert adverse["status"] == "completed", adverse
    adverse_cells = cells(output / "jobs" / adverse_request["job_id"] / "map.bundle")
    assert 3 in adverse_cells.values() and all(value != 1 for value in adverse_cells.values())
    corrupt_path = inputs / "corrupt-parent.bundle"
    with zipfile.ZipFile(bundle) as original_archive:
        with zipfile.ZipFile(corrupt_path, "x") as corrupt:
            changed = False
            for name in original_archive.namelist():
                content = original_archive.read(name)
                if name.startswith("tiles/") and not changed:
                    content = content[:-1] + bytes([content[-1] ^ 1])
                    changed = True
                corrupt.writestr(name, content)
    corrupt_request = {
        **incremental_request,
        "job_id": uuid.uuid4().hex,
        "parent": {
            **incremental_request["parent"],
            "path": corrupt_path.name,
            "content_digest": "sha256:" + hashlib.sha256(corrupt_path.read_bytes()).hexdigest(),
        },
    }
    corrupted = job(python, output, inputs, corrupt_request, "corrupt-parent")
    assert corrupted["status"] == "unavailable" and "tile bytes differ" in corrupted["reason"], (
        corrupted
    )
    nonrigid_request = {
        **request,
        "job_id": uuid.uuid4().hex,
        "inputs": [request["inputs"][0], source(inputs / "nonrigid.json", "nonrigid-frame")],
    }
    nonrigid = job(python, output, inputs, nonrigid_request, "low-overlap")
    assert nonrigid["status"] == "unavailable", nonrigid
    timeout_request = {
        **request,
        "job_id": uuid.uuid4().hex,
        "policy": {
            **policy(),
            "yaw_min": -3.0,
            "yaw_max": 3.0,
            "yaw_step": 0.05,
            "translation_bound_m": 0.5,
            "translation_step_m": 0.025,
            "max_hypotheses": 300000,
            "timeout_s": 1,
        },
    }
    timed_out = job(python, output, inputs, timeout_request, "timeout")
    assert timed_out["status"] == "unknown", timed_out
    timeout_directory = output / "jobs" / timeout_request["job_id"]
    original_worker = (timeout_directory / "worker-started.json").read_bytes()
    original_intent = (timeout_directory / "intent.json").read_bytes()
    assert job(python, output, inputs, timeout_request, "timeout-replay") == timed_out
    assert (timeout_directory / "worker-started.json").read_bytes() == original_worker
    assert (timeout_directory / "intent.json").read_bytes() == original_intent
    for calculated in (result, incremental, adverse):
        assert calculated["timing_assurance"] == "original-host-monotonic-budget"
        assert calculated["manifest"]["current_node_localization"] is None
        assert calculated["manifest"]["covariance"] is None
        assert calculated["manifest"]["unobserved_space"] == "unknown"
        assert calculated["manifest"]["navigation_safety_assurance"] is None
    return {
        "passed": True,
        "devices": 0,
        "simulators": 0,
        "model_calls": 0,
        "original_sha256": original_sha256,
        "projection": truth,
        "independent_errors": errors,
        "loop_edges": 3,
        "parent_unchanged": True,
        "positive_unknown_not_filled": True,
        "adversarial_free_retained_as_conflict": True,
        "corrupt_parent_rejected": True,
        "low_overlap_rejected": True,
        "timeout_budget_not_renewed": True,
        "original_monotonic_budget": True,
        "loop_artifact": result["artifact"],
        "incremental_artifact": incremental["artifact"],
        "algorithm_code_digest": manifest["algorithm"]["code_digest"],
        "dependencies": manifest["algorithm"]["versions"],
        "hardware_evidence": "HardwareEvidencePending",
    }


def main() -> None:
    """Keep every native result and failure in a new private evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.python, args.source, args.source_sha256, args.output)
    except Exception as error:
        if args.output.is_dir():
            (args.output / "result.json").write_text(
                json.dumps({"passed": False, "error": str(error)[:8192]}, indent=2) + "\n"
            )
        raise
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
