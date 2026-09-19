"""Frozen B1 input workload extraction for the generic RoboGuide runner.

The Formal B1 RoboGuide arm must be driven by the workload declared in its
frozen high-level input document — dataset identity, episode, seed, and the
text instruction — instead of a scenario-embedded episode. This module is
the single parser/validator the scenario runner consumes before launching
any component: the exact v0.1 schema marker and every workload-authoritative
field (including the scene identity) are checked here, with typed errors,
so a missing, mistyped, or wrong-schema workload fails in seconds with a
machine-stable reason instead of deep inside a simulator process. The B1
provenance verifier delegates to the same extraction so the contract holds
even when a run bypasses this runner.

Downstream identity binding is NOT re-implemented here: the bridge's
authoritative semantic evidence carries the runtime-observed episode,
scene, and dataset digest, and the B1 provenance verifier requires those
to equal the frozen input's fields. This module only guarantees the runner
actually forwards what the input declares.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

B1_INPUT_SCHEMA = "roboguide.e1.b1-input/v0.1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class B1WorkloadError(ValueError):
    """Report one invalid frozen B1 input with a machine-stable reason.

    Attributes:
        field: The offending field path (for example ``seed`` or
            ``dataset_sha256``); ``document`` when the document itself is
            unusable.
    """

    def __init__(self, field: str, message: str) -> None:
        """Store the field path and the descriptive message."""
        super().__init__(f"{field}: {message}")
        self.field = field


@dataclass(frozen=True, slots=True)
class B1Workload:
    """The validated workload fields one Formal B1 run executes.

    Attributes:
        episode_id: The benchmark episode the bridge must pin.
        seed: The simulator seed forwarded as ``habitat.seed``; habitat's
            iterator ignores seed 0, so zero is rejected.
        dataset_revision: The monolithic dataset revision name.
        dataset_sha256: The expected sha256 over the dataset gzip bytes.
        scene_id: The expected scene identity; mandatory because the
            provenance verifier binds it against the bridge's runtime
            semantic evidence, so an input without it is not a workload.
        instruction: The high-level text instruction for Mission
            Intelligence.
    """

    episode_id: str
    seed: int
    dataset_revision: str
    dataset_sha256: str
    scene_id: str
    instruction: str


def _require_text(document: dict[str, Any], field: str) -> str:
    """Require one nonblank string field.

    Raises:
        B1WorkloadError: When the field is missing, not a string, or blank.
    """
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise B1WorkloadError(field, "must be a nonblank string")
    return value


def extract_b1_workload(document: Any) -> B1Workload:
    """Validate one frozen B1 input document and return its workload.

    Args:
        document: The decoded ``b1-input`` JSON document.

    Returns:
        The validated workload fields.

    Raises:
        B1WorkloadError: On any missing, mistyped, or inconsistent field.
            The document must carry the exact v0.1 schema marker; unknown
            extra fields are tolerated so future additive revisions do not
            break older runners, but every workload-authoritative field —
            including the scene identity — is mandatory.
    """
    if not isinstance(document, dict):
        raise B1WorkloadError("document", "must be a JSON object")
    schema = document.get("schema")
    if schema != B1_INPUT_SCHEMA:
        raise B1WorkloadError("schema", f"must be exactly {B1_INPUT_SCHEMA!r}")
    episode_id = _require_text(document, "episode_id")
    seed = document.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise B1WorkloadError("seed", "must be an integer")
    if seed <= 0:
        # habitat's episode iterator ignores seed 0, and negative seeds are
        # not meaningful simulator seeds; either would silently break the
        # reproducibility contract the workload claims.
        raise B1WorkloadError("seed", "must be a positive integer (habitat ignores seed 0)")
    dataset_revision = _require_text(document, "dataset_revision")
    dataset_sha256 = _require_text(document, "dataset_sha256")
    if _HEX64.fullmatch(dataset_sha256) is None:
        raise B1WorkloadError("dataset_sha256", "must be 64 lowercase hex characters")
    scene_id = _require_text(document, "scene_id")
    instruction = _require_text(document, "instruction")
    return B1Workload(
        episode_id=episode_id,
        seed=seed,
        dataset_revision=dataset_revision,
        dataset_sha256=dataset_sha256,
        scene_id=scene_id,
        instruction=instruction,
    )


def load_b1_workload(path: Path) -> B1Workload:
    """Load and validate one frozen B1 input file.

    Raises:
        B1WorkloadError: When the file is unreadable or invalid.
        OSError: Propagated from the underlying read.
    """
    return extract_b1_workload(json.loads(Path(path).read_text(encoding="utf-8")))


def _main(argv: list[str] | None = None) -> int:
    """Print one validated workload as key=value lines for the shell runner.

    Exit code 2 with ``field: message`` on stderr for any invalid input so
    scenario scripts can attribute the failure before launching components.
    """
    arguments = argv if argv is not None else sys.argv[1:]
    if len(arguments) != 1:
        print("usage: python -m roboguide_eval.b1_workload <b1-input.json>", file=sys.stderr)
        return 2
    try:
        workload = load_b1_workload(Path(arguments[0]))
    except (B1WorkloadError, OSError, json.JSONDecodeError) as error:
        print(f"invalid B1 workload: {error}", file=sys.stderr)
        return 2
    for name, value in (
        ("episode_id", workload.episode_id),
        ("seed", workload.seed),
        ("dataset_revision", workload.dataset_revision),
        ("dataset_sha256", workload.dataset_sha256),
        ("scene_id", workload.scene_id),
    ):
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
