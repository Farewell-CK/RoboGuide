"""Read-only deployment stop/continuation declarations; never permission to repeat work."""

from __future__ import annotations

from .model import SUPPORTED_OPERATIONS

RECOVERY_PROFILE_SCHEMA = "roboguide.local-execution-recovery/v0.1"


def execution_recovery_profile(
    *, shared_world: bool, retain_stopped_session: bool = False
) -> dict[str, object]:
    """Declare actual joint stop and explicitly enabled retained-world continuation.

    Shared endpoints cancel the whole joint segment. A standalone adapter has
    one active execution, but its next invocation resets rather than retaining
    the stopped world. Optional shared-world continuation preserves that world
    but still requires Group stop coordination; it never supports Role retry.
    Normal Control-owned next-Task serial execution remains a separate ability.
    This function never reads simulator state, calls a model or changes execution.
    """
    operations: list[dict[str, object]] = []
    for canonical in SUPPORTED_OPERATIONS:
        namespace_name, version = canonical.rsplit("@", 1)
        namespace, name = namespace_name.rsplit(".", 1)
        operations.append(
            {
                "operation": {"namespace": namespace, "name": name, "version": version},
                "stop_scope": "execution-group" if shared_world else "execution",
                "continuation": (
                    "repeat-after-stop"
                    if shared_world and retain_stopped_session
                    else "unsupported"
                ),
            }
        )
    return {"schema_version": RECOVERY_PROFILE_SCHEMA, "operations": operations}
