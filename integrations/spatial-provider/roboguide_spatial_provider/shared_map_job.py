"""Assemble the T08 shared-map job without widening the occupancy contracts.

The upstream Artifact preparation and preparation-use checks remain external to
this module.  A caller supplies both an artifact identity and the normalized
occupancy bytes derived from it. The invoking service validates its current-use
grant separately from the closed occupancy job request.

``build_shared_map_job`` is the lock-free assembly seam.  Its request can be
passed to the maintained ``python -m roboguide_spatial_provider --request ...
--source-root ... --store ...`` invocation after ``stage_shared_map_sources``
has sealed N and M in the isolated source root.  ``run_shared_map_job`` is the
convenience path for a caller whose dedicated store is free of the provider lock.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .contracts import canonical, digest, exact, fields, number
from .occupancy_contracts import OccupancyJob, OccupancyPolicy, grid
from .occupancy_export import NavigationMap, export_navigation_map
from .occupancy_input import NormalizedOccupancy, normalize_occupancy_bundle
from .storage import immutable, read_source

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class SharedMapSource:
    """Exact upstream identity and its normalized metre-grid representation."""

    role: Literal["n", "m"]
    artifact_id: str
    artifact_version: str
    artifact_digest: str
    frame_id: str
    revision_id: str
    normalized: NormalizedOccupancy

    @classmethod
    def from_shared_bundle(
        cls, content: bytes, typed_map: dict[str, Any], policy: OccupancyPolicy
    ) -> SharedMapSource:
        """Bind copied M bytes to the original typed map before registering them."""
        if type(typed_map.get("byte_size")) is not int or typed_map["byte_size"] != len(content):
            raise ValueError("shared map byte count differs from the typed manifest")
        content_digest = exact(typed_map.get("content_digest"), "shared map content_digest")
        frame_id = exact(typed_map.get("root_frame"), "shared map root_frame")
        revision_id = exact(typed_map.get("revision_id"), "shared map revision_id")
        normalized = normalize_occupancy_bundle(
            content,
            expected_digest=content_digest,
            frame_id=frame_id,
            revision_id=revision_id,
            resolution_meters=policy.resolution_meters,
            max_bytes=policy.max_input_bytes,
            max_tiles=policy.max_tiles,
            max_cells=policy.max_cells,
        )
        return cls(
            "m",
            exact(typed_map.get("map_id"), "shared map_id"),
            revision_id,
            content_digest,
            frame_id,
            revision_id,
            normalized,
        )

    def __post_init__(self) -> None:
        """Bind normalized bytes and conversion evidence to the supplied artifact."""
        if self.role not in {"n", "m"}:
            raise ValueError("shared-map role must be exactly n or m")
        exact(self.artifact_id, "artifact_id")
        exact(self.artifact_version, "artifact_version")
        exact(self.revision_id, "artifact revision_id")
        final_frame = exact(self.frame_id, "artifact frame_id")
        if (
            not isinstance(self.artifact_digest, str)
            or _DIGEST.fullmatch(self.artifact_digest) is None
        ):
            raise ValueError("artifact_digest must be an exact lowercase SHA-256")
        receipt = self.normalized.receipt
        fields(
            receipt,
            {
                "schema_version",
                "format",
                "frame_id",
                "original_units",
                "sources",
                "output",
                "classification",
            },
            {"ros_map"},
        )
        if receipt["schema_version"] != "roboguide.occupancy-normalization/v0.1":
            raise ValueError("normalized source conversion schema is unsupported")
        if receipt["frame_id"] != final_frame:
            raise ValueError("normalized source frame differs from its artifact binding")
        output = fields(receipt["output"], {"content_digest", "byte_size", "units"})
        if (
            output["content_digest"] != self.normalized.content_digest
            or output["byte_size"] != len(self.normalized.content)
            or output["units"] != "m"
        ):
            raise ValueError("normalized bytes differ from their conversion receipt")
        if not isinstance(receipt["sources"], list) or not receipt["sources"]:
            raise ValueError("normalized source receipt lacks original identities")
        source_digests = []
        for source in receipt["sources"]:
            identity = fields(source, {"role", "content_digest", "byte_size"})
            source_digest = identity["content_digest"]
            if not isinstance(source_digest, str) or _DIGEST.fullmatch(source_digest) is None:
                raise ValueError("normalized source receipt has an invalid digest")
            source_digests.append(source_digest)
        if (
            self.artifact_digest not in source_digests
            and self.artifact_digest != self.normalized.content_digest
        ):
            raise ValueError("artifact digest is absent from the conversion receipt")
        grid(self.normalized.grid, 1_000_000)
        if self.normalized.content != canonical(self.normalized.grid):
            raise ValueError("normalized source bytes are not canonical occupancy JSON")

    @property
    def conversion_identity(self) -> dict[str, Any]:
        """Return stable source/output conversion identities, not job permission."""
        receipt = self.normalized.receipt
        return {
            "schema_version": receipt["schema_version"],
            "format": receipt["format"],
            "content_digest": digest(canonical(receipt)),
            "source_digests": [source["content_digest"] for source in receipt["sources"]],
            "output_digest": self.normalized.content_digest,
        }

    def identity(self) -> dict[str, Any]:
        """Expose every source/conversion identity consumed by the assembled job."""
        return {
            "role": self.role,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "artifact_digest": self.artifact_digest,
            "frame_id": self.frame_id,
            "revision_id": self.revision_id,
            "normalized_content_digest": self.normalized.content_digest,
            "normalized_byte_size": len(self.normalized.content),
            "conversion": self.conversion_identity,
        }


@dataclass(frozen=True)
class AssembledSharedMapJob:
    """Pure, immutable occupancy request plus the identities it was built from."""

    request: dict[str, Any]
    n: SharedMapSource
    m: SharedMapSource
    policy: OccupancyPolicy

    @property
    def job_id(self) -> str:
        """Return the stable caller-selected admission identity."""
        return exact(self.request["job_id"], "job_id", 64)

    @property
    def request_digest(self) -> str:
        """Return the exact bytes identity used by original-job replay."""
        return digest(canonical(self.request))

    def source_identities(self) -> list[dict[str, Any]]:
        """Return N then M identities without adding them to the closed request."""
        return [self.n.identity(), self.m.identity()]


@dataclass(frozen=True)
class SharedMapJobOutcome:
    """Original occupancy observation, relationship and optional navigation export."""

    assembly: AssembledSharedMapJob
    occupancy_result: dict[str, Any]
    relationship: dict[str, Any] | None
    navigation_map: NavigationMap | None

    def receipt(self) -> dict[str, Any]:
        """Expose local identities while leaving current-use authority opaque and upstream."""
        navigation: dict[str, Any] | None = None
        if self.navigation_map is not None:
            navigation = json.loads(self.navigation_map.receipt)
        return {
            "schema_version": "roboguide.shared-map-job-result/v0.1",
            "job_id": self.assembly.job_id,
            "request_digest": self.assembly.request_digest,
            "occupancy_result": self.occupancy_result,
            "sources": self.assembly.source_identities(),
            "relationship": self.relationship,
            "navigation_export": navigation,
            "authority": {
                "artifact_metadata_grants_use": False,
            },
        }


def build_shared_map_job(
    *,
    job_id: str,
    n: SharedMapSource,
    m: SharedMapSource,
    policy: OccupancyPolicy | dict[str, Any],
) -> AssembledSharedMapJob:
    """Build the unchanged planar occupancy request with N as the first anchor."""
    if n.role != "n" or m.role != "m":
        raise ValueError("shared-map sources must bind N and M in their declared roles")
    parsed_policy = policy if isinstance(policy, OccupancyPolicy) else OccupancyPolicy.parse(policy)
    if parsed_policy.free_coverage_margin_m != 0:
        raise ValueError("shared-map planning export requires a zero source-cell margin")
    request = {
        "schema_version": "roboguide.spatial-occupancy-job/v0.1",
        "job_id": exact(job_id, "job_id", 64),
        "frame_id": n.frame_id,
        "policy": asdict(parsed_policy),
        "inputs": [
            {
                "source_id": "n",
                "path": "n.occupancy.json",
                "content_digest": n.normalized.content_digest,
                "frame_id": n.frame_id,
                "units": "m",
                "format": "occupancy-json",
            },
            {
                "source_id": "m",
                "path": "m.occupancy.json",
                "content_digest": m.normalized.content_digest,
                "frame_id": m.frame_id,
                "units": "m",
                "format": "occupancy-json",
            },
        ],
    }
    OccupancyJob.parse(request)
    return AssembledSharedMapJob(request, n, m, parsed_policy)


def stage_shared_map_sources(assembly: AssembledSharedMapJob, source_root: Path) -> None:
    """Seal N then M into an isolated source root without following symlinks."""
    source_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("shared-map source root must be a real directory")
    expected = {"n.occupancy.json", "m.occupancy.json", "binding.json"}
    if any(child.name not in expected for child in source_root.iterdir()):
        raise ValueError("shared-map source root is not isolated to this job")
    binding = {"request_digest": assembly.request_digest, "sources": assembly.source_identities()}
    immutable(source_root / "binding.json", canonical(binding))
    immutable(source_root / "n.occupancy.json", assembly.n.normalized.content)
    immutable(source_root / "m.occupancy.json", assembly.m.normalized.content)


def run_shared_map_job(
    assembly: AssembledSharedMapJob,
    *,
    source_root: Path,
    store: Path,
    run_occupancy: Callable[[dict[str, Any], Path, Path], dict[str, Any]] | None = None,
    observe_occupancy: Callable[[Path], dict[str, Any]] | None = None,
    worker_python: str | os.PathLike[str] | None = None,
    worker_dependencies: Mapping[str, str] | None = None,
) -> SharedMapJobOutcome:
    """Stage and run the original occupancy job, then export only completed bytes.

    The caller owns preparation and use authority for production inputs.
    """
    stage_shared_map_sources(assembly, source_root)
    if (run_occupancy is None) != (observe_occupancy is None):
        raise ValueError("run and observe hooks must either both be supplied or both be omitted")
    if run_occupancy is not None and (worker_python is not None or worker_dependencies is not None):
        raise ValueError("worker runtime options apply only to the default Spatial provider")
    if run_occupancy is None or observe_occupancy is None:
        from .__main__ import observe as provider_observe
        from .__main__ import run as provider_run

        if worker_python is None and worker_dependencies is None:
            run_occupancy = provider_run
        else:

            def run_bound_provider(
                request: dict[str, Any], source_root: Path, store: Path
            ) -> dict[str, Any]:
                """Bind exact worker runtime inputs without changing default callers."""
                return provider_run(
                    request,
                    source_root,
                    store,
                    worker_python=worker_python,
                    worker_dependencies=worker_dependencies,
                )

            run_occupancy = run_bound_provider
        observe_occupancy = provider_observe
    result = run_occupancy(assembly.request, source_root, store)
    directory = store / assembly.job_id
    observed = observe_occupancy(directory)
    if observed != result:
        raise ValueError("exact shared-map replay differs from the original observation")
    if (
        result.get("job_id") != assembly.job_id
        or result.get("request_digest") != assembly.request_digest
    ):
        raise ValueError("occupancy result differs from the assembled job identity")
    if result.get("status") != "completed":
        return SharedMapJobOutcome(assembly, result, None, None)

    artifact = result.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("path") != "map.bundle":
        raise ValueError("completed occupancy result lacks its exact map bundle")
    content = read_source(directory, "map.bundle", assembly.policy.max_output_bytes)
    if digest(content) != artifact.get("content_digest") or len(content) != artifact.get(
        "byte_size"
    ):
        raise ValueError("completed occupancy bundle differs from its original proof")
    relationship = _relationship(assembly, result)
    navigation_map = export_navigation_map(
        content,
        content_digest=artifact["content_digest"],
        frame_id=assembly.n.frame_id,
        revision_id=assembly.job_id,
        resolution_meters=assembly.policy.resolution_meters,
        planning_inputs=(assembly.n.normalized.content, assembly.m.normalized.content),
    )
    return SharedMapJobOutcome(assembly, result, relationship, navigation_map)


def _relationship(assembly: AssembledSharedMapJob, result: dict[str, Any]) -> dict[str, Any]:
    """Expose the measured M-to-N relation beside complete source identities."""
    manifest = result.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("completed occupancy result lacks its validated manifest")
    if (
        manifest.get("schema_version") != "roboguide.spatial-occupancy-map/v0.1"
        or manifest.get("frame_id") != assembly.n.frame_id
        or manifest.get("revision_id") != assembly.job_id
        or manifest.get("resolution_meters") != assembly.policy.resolution_meters
        or manifest.get("unobserved_space") != "unknown"
    ):
        raise ValueError("occupancy manifest differs from the assembled frame/revision")
    sources = manifest.get("sources")
    expected_sources = [
        {
            "source_id": "n",
            "frame_id": assembly.n.frame_id,
            "content_digest": assembly.n.normalized.content_digest,
            "units": "m",
            "format": "occupancy-json",
            "byte_size": len(assembly.n.normalized.content),
            "cell_count": len(assembly.n.normalized.grid["cells"]),
        },
        {
            "source_id": "m",
            "frame_id": assembly.m.frame_id,
            "content_digest": assembly.m.normalized.content_digest,
            "units": "m",
            "format": "occupancy-json",
            "byte_size": len(assembly.m.normalized.content),
            "cell_count": len(assembly.m.normalized.grid["cells"]),
        },
    ]
    if not isinstance(sources, list) or sources != expected_sources:
        raise ValueError("occupancy manifest source identities differ from the assembled job")
    transformations = manifest.get("transformations_to_anchor")
    if not isinstance(transformations, list) or len(transformations) != 2:
        raise ValueError("shared-map manifest must expose one transform per source")
    n_pose = _matrix(transformations[0], "N transform")
    m_pose = _matrix(transformations[1], "M transform")
    if n_pose != [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]:
        raise ValueError("shared-map N was not retained as the immutable anchor")
    core = {
        "schema_version": "roboguide.shared-map-relationship/v0.1",
        "n_source": assembly.n.identity(),
        "m_source": assembly.m.identity(),
        "T_N_from_M": m_pose,
        "T_M_from_N": _inverse_planar(m_pose),
        "registration": manifest.get("registration"),
        "observed_cell_count": manifest.get("observed_cell_count"),
        "effective_scope": None,
        "unobserved_space": "unknown",
    }
    return {**core, "content_digest": digest(canonical(core))}


def _matrix(value: Any, name: str) -> list[list[float]]:
    """Validate one finite planar homogeneous transform from the original manifest."""
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must be a 3x3 matrix")
    rows = []
    for index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError(f"{name} must be a 3x3 matrix")
        rows.append([number(item, f"{name}[{index}]", -1e12, 1e12) for item in row])
    if rows[2] != [0.0, 0.0, 1.0]:
        raise ValueError(f"{name} is not planar homogeneous")
    return rows


def _inverse_planar(value: list[list[float]]) -> list[list[float]]:
    """Invert an admitted planar homogeneous transform for explicit direction binding."""
    determinant = value[0][0] * value[1][1] - value[0][1] * value[1][0]
    if abs(determinant) <= 1e-15:
        raise ValueError("shared-map relationship is singular")
    inverse = [
        [
            value[1][1] / determinant,
            -value[0][1] / determinant,
            0.0,
        ],
        [
            -value[1][0] / determinant,
            value[0][0] / determinant,
            0.0,
        ],
        [0.0, 0.0, 1.0],
    ]
    inverse[0][2] = -(inverse[0][0] * value[0][2] + inverse[0][1] * value[1][2])
    inverse[1][2] = -(inverse[1][0] * value[0][2] + inverse[1][1] * value[1][2])
    return inverse
