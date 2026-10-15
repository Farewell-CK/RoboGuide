"""Check actual file geometry and rigid coordinates with zero devices."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def execute(python: Path, arguments: list[str], output: Path) -> dict[str, Any]:
    """Run the actual independent provider runtime under a bounded outer software-check deadline."""
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "integrations/spatial-provider"),
            "CUDA_VISIBLE_DEVICES": "",
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        }
    )
    with output.open("xb") as result:
        subprocess.run(
            [str(python), *arguments],
            cwd=ROOT,
            env=environment,
            stdout=result,
            stderr=subprocess.PIPE,
            check=True,
            timeout=180,
        )
    value = json.loads(output.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("spatial result is not an object")
    return value


def job(
    python: Path, output: Path, source_root: Path, request: dict[str, Any], name: str
) -> dict[str, Any]:
    """Submit one original job with its request, result and worker history."""
    path = output / f"{name}-request.json"
    path.write_text(json.dumps(request, indent=2) + "\n")
    return execute(
        python,
        [
            "-m",
            "roboguide_spatial_provider",
            "--request",
            str(path),
            "--source-root",
            str(source_root),
            "--store",
            str(output / "jobs"),
        ],
        output / f"{name}-result.json",
    )


def input_file(source_id: str, path: Path, root: Path, frame: str) -> dict[str, Any]:
    """Bind actual source bytes and their declared file coordinate frame."""
    return {
        "source_id": source_id,
        "path": str(path.relative_to(root)),
        "content_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "frame_id": frame,
        "units": "m",
        "format": path.suffix.lstrip("."),
    }


def check_transform(
    actual: list[list[float]], original_to_derived: list[list[float]]
) -> dict[str, float]:
    """Compare the estimated transform with an independent exact coordinate transform."""
    rotation = [[original_to_derived[column][row] for column in range(3)] for row in range(3)]
    translation = [
        -sum(rotation[row][column] * original_to_derived[column][3] for column in range(3))
        for row in range(3)
    ]
    distance = math.sqrt(sum((actual[row][3] - translation[row]) ** 2 for row in range(3)))
    relative = [
        [sum(rotation[k][row] * actual[k][column] for k in range(3)) for column in range(3)]
        for row in range(3)
    ]
    angle = math.acos(max(-1.0, min(1.0, (sum(relative[i][i] for i in range(3)) - 1) / 2)))
    if distance > 0.02 or angle > 0.02:
        raise ValueError(f"independent coordinate error exceeds bound: {distance}m/{angle}rad")
    return {"translation_error_m": distance, "rotation_error_rad": angle}


def run(python: Path, source_manifest: Path, policy_file: Path, output: Path) -> dict[str, Any]:
    """Check reference files, known coordinates, original replay and parent hashes."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    source = json.loads(source_manifest.read_bytes())
    files = source["files"]
    if len(files) < 3:
        raise ValueError("reference check requires three attributed actual point-cloud files")
    for index, description in enumerate(files[:3]):
        path = Path(description["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != description["sha256"]:
            raise ValueError("reference source hash differs")
        shutil.copyfile(path, inputs / f"source-{index}.pcd")
    policy = json.loads(policy_file.read_bytes())
    if "policy" in policy:
        policy = policy["policy"]
    pair_request = {
        "schema_version": "roboguide.spatial-file-job/v0.1",
        "job_id": uuid.uuid4().hex,
        "frame_id": "reference-file-frame",
        "inputs": [
            input_file(
                f"source-{index}",
                inputs / f"source-{index}.pcd",
                inputs,
                "reference-file-frame" if index == 0 else f"source-frame-{index}",
            )
            for index in range(2)
        ],
        "policy": policy,
    }
    pair = job(python, output, inputs, pair_request, "pair")
    assert pair["status"] == "completed", pair
    pair_bundle = output / "jobs" / pair_request["job_id"] / "map.bundle"
    parent_hash = hashlib.sha256(pair_bundle.read_bytes()).hexdigest()
    shutil.copyfile(pair_bundle, inputs / "parent.bundle")
    incremental_request = {
        **pair_request,
        "job_id": uuid.uuid4().hex,
        "inputs": [
            input_file("third-original-source", inputs / "source-2.pcd", inputs, "third-file-frame")
        ],
        "parent": {
            "path": "parent.bundle",
            "content_digest": pair["artifact"]["content_digest"],
            "revision_id": pair_request["job_id"],
            "frame_id": pair_request["frame_id"],
        },
    }
    incremental = job(python, output, inputs, incremental_request, "incremental")
    assert incremental["status"] == "completed", incremental
    assert hashlib.sha256(pair_bundle.read_bytes()).hexdigest() == parent_hash
    prep = r"""import json,math,sys
from pathlib import Path
import open3d as o,numpy as np
cloud=o.io.read_point_cloud(sys.argv[1])
a,b,c=0.27,-0.13,0.19
rx=np.array([[1,0,0],[0,math.cos(a),-math.sin(a)],[0,math.sin(a),math.cos(a)]])
ry=np.array([[math.cos(b),0,math.sin(b)],[0,1,0],[-math.sin(b),0,math.cos(b)]])
rz=np.array([[math.cos(c),-math.sin(c),0],[math.sin(c),math.cos(c),0],[0,0,1]])
t=np.eye(4);t[:3,:3]=rz@ry@rx;t[:3,3]=[0.31,-0.17,0.09]
cloud.transform(t)
assert o.io.write_point_cloud(sys.argv[2],cloud,write_ascii=False,compressed=False)
points=np.asarray(cloud.points).copy()
cloud.points=o.utility.Vector3dVector(points*100)
assert o.io.write_point_cloud(sys.argv[3],cloud,write_ascii=False,compressed=False)
points[:,2]=0
cloud.points=o.utility.Vector3dVector(points)
assert o.io.write_point_cloud(sys.argv[4],cloud,write_ascii=False,compressed=False)
points=np.asarray(o.io.read_point_cloud(sys.argv[1]).points).copy()
points*=np.array([9.0,4.0,2.0])
cloud.points=o.utility.Vector3dVector(points)
assert o.io.write_point_cloud(sys.argv[5],cloud,write_ascii=False,compressed=False)
print(json.dumps({"original_to_derived":t.tolist()}))
"""
    truth = execute(
        python,
        [
            "-c",
            prep,
            str(inputs / "source-0.pcd"),
            str(inputs / "derived.ply"),
            str(inputs / "centimetres.ply"),
            str(inputs / "planar.ply"),
            str(inputs / "nonrigid.ply"),
        ],
        output / "coordinate-truth.json",
    )
    coordinate_request = {
        **pair_request,
        "job_id": uuid.uuid4().hex,
        "inputs": [
            input_file(
                "original-coordinate-file", inputs / "source-0.pcd", inputs, "reference-file-frame"
            ),
            input_file(
                "derived-coordinate-file",
                inputs / "derived.ply",
                inputs,
                "independent-derived-frame",
            ),
        ],
    }
    coordinate = job(python, output, inputs, coordinate_request, "coordinate")
    assert coordinate["status"] == "completed", coordinate
    error = check_transform(
        coordinate["manifest"]["transformations_to_anchor"][1], truth["original_to_derived"]
    )
    directory = output / "jobs" / coordinate_request["job_id"]
    original_worker = (directory / "worker-started.json").read_bytes()
    original_intent = (directory / "intent.json").read_bytes()
    replay = job(python, output, inputs, coordinate_request, "coordinate-replay")
    assert replay == coordinate
    assert (directory / "worker-started.json").read_bytes() == original_worker
    assert (directory / "intent.json").read_bytes() == original_intent
    centimetres = input_file("metric-conversion", inputs / "centimetres.ply", inputs, "cm-frame")
    centimetres["units"] = "cm"
    units_request = {
        **coordinate_request,
        "job_id": uuid.uuid4().hex,
        "inputs": [coordinate_request["inputs"][0], centimetres],
    }
    units = job(python, output, inputs, units_request, "units")
    assert units["status"] == "completed", units
    units_error = check_transform(
        units["manifest"]["transformations_to_anchor"][1], truth["original_to_derived"]
    )
    loop_request = {
        **pair_request,
        "job_id": uuid.uuid4().hex,
        "inputs": [
            *pair_request["inputs"],
            input_file("loop-third-source", inputs / "source-2.pcd", inputs, "loop-third-frame"),
        ],
    }
    loop = job(python, output, inputs, loop_request, "loop")
    assert loop["status"] == "completed" and len(loop["manifest"]["registration"]) == 3, loop
    negative_results = {}
    for label, file, min_fitness in (
        ("degenerate", "planar.ply", 0.35),
        ("low-overlap", "nonrigid.ply", 0.9),
    ):
        negative = {
            **pair_request,
            "job_id": uuid.uuid4().hex,
            "policy": {**policy, "min_fitness": min_fitness},
            "inputs": [
                pair_request["inputs"][0],
                input_file(label, inputs / file, inputs, f"{label}-frame"),
            ],
        }
        rejected = job(python, output, inputs, negative, label)
        assert rejected["status"] == "unavailable", rejected
        assert not (output / "jobs" / negative["job_id"] / "computed.json").exists()
        negative_results[label] = rejected["reason"]
    with zipfile.ZipFile(pair_bundle) as original:
        with zipfile.ZipFile(inputs / "corrupt-parent.bundle", "x") as corrupt:
            changed = False
            for name in original.namelist():
                content = original.read(name)
                if name.endswith(".ply") and not changed:
                    content = content[:-1] + bytes([content[-1] ^ 1])
                    changed = True
                corrupt.writestr(name, content)
    corrupt_request = {
        **incremental_request,
        "job_id": uuid.uuid4().hex,
        "parent": {
            **incremental_request["parent"],
            "path": "corrupt-parent.bundle",
            "content_digest": "sha256:"
            + hashlib.sha256((inputs / "corrupt-parent.bundle").read_bytes()).hexdigest(),
        },
    }
    corrupt_result = job(python, output, inputs, corrupt_request, "corrupt-parent")
    assert corrupt_result["status"] == "unavailable", corrupt_result
    negative_results["corrupt-parent"] = corrupt_result["reason"]
    timeout_request = {
        **pair_request,
        "job_id": uuid.uuid4().hex,
        "policy": {**policy, "timeout_s": 1},
    }
    timed_out = job(python, output, inputs, timeout_request, "timeout")
    assert timed_out["status"] == "unknown", timed_out
    timeout_directory = output / "jobs" / timeout_request["job_id"]
    timeout_intent = (timeout_directory / "intent.json").read_bytes()
    timeout_worker = (timeout_directory / "worker-started.json").read_bytes()
    assert job(python, output, inputs, timeout_request, "timeout-replay") == timed_out
    assert (timeout_directory / "intent.json").read_bytes() == timeout_intent
    assert (timeout_directory / "worker-started.json").read_bytes() == timeout_worker
    for result in (pair, incremental, coordinate, units, loop):
        assert result["manifest"]["current_node_localization"] is None
        assert result["manifest"]["covariance"] is None
        assert result["manifest"]["unobserved_space"] == "unknown"
    return {
        "passed": True,
        "devices": 0,
        "simulators": 0,
        "model_calls": 0,
        "source_manifest_sha256": hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
        "pair_artifact": pair["artifact"],
        "incremental_artifact": incremental["artifact"],
        "parent_unchanged": True,
        "coordinate_error": error,
        "unit_conversion_error": units_error,
        "loop_edges": len(loop["manifest"]["registration"]),
        "negative_cases": negative_results,
        "original_timeout_not_renewed": True,
        "original_worker_not_repeated": True,
        "algorithm_code_digest": coordinate["manifest"]["algorithm"]["code_digest"],
        "dependencies": coordinate["manifest"]["algorithm"]["versions"],
        "native_binary_digests": coordinate["manifest"]["algorithm"]["native_binary_digests"],
        "hardware_evidence": "HardwareEvidencePending",
    }


def main() -> None:
    """Retain bounded actual software evidence and every failed check outside the repository."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.python, args.source_manifest, args.policy, args.output)
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
