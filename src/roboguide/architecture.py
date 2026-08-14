"""Machine-readable description of the V1.1 architecture baseline.

This is a baseline manifest, not a wire schema or an execution API.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaneSpec:
    """A logical plane and its repository ownership."""

    name: str
    package: str
    responsibility: str


ARCHITECTURE_VERSION = "V1.1"

CORE_LOOP = (
    "Observe",
    "Reason",
    "Schedule",
    "Coordinate",
    "Execute",
    "Reconcile",
)

PLANES = (
    PlaneSpec(
        name="Mission / Intelligence",
        package="roboguide.mission_intelligence",
        responsibility="Understand intent, manage missions, decompose tasks, and build task graphs.",
    ),
    PlaneSpec(
        name="Embodied Control",
        package="roboguide.control_plane",
        responsibility="Match capabilities, jointly schedule resources, coordinate execution, and recover.",
    ),
    PlaneSpec(
        name="Embodied State & Memory",
        package="roboguide.state_memory",
        responsibility="Provide the logical current-state view and distributed time-based context.",
    ),
    PlaneSpec(
        name="Distributed Embodied Runtime",
        package="roboguide.runtime",
        responsibility="Run capabilities, distribute execution and data, and connect local runtimes.",
    ),
)


def baseline_summary() -> str:
    """Return a concise human-readable baseline summary."""

    plane_names = ", ".join(plane.name for plane in PLANES)
    loop = " -> ".join(CORE_LOOP)
    return f"RoboGuide architecture {ARCHITECTURE_VERSION}: {plane_names}\nCore loop: {loop}"
