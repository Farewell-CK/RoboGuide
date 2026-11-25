"""EAIOS-neutral RoboGuide multi-robot synchronization provider (`sync-provider`)."""

from __future__ import annotations

__version__ = "0.1.0"

from .schemas import (
    CHOREOGRAPHY_SCHEMA,
    OP_CHOREOGRAPHY_PLAY,
    OP_SYNC_FOLLOW,
    SAFE_STATE_OBSERVATION_SCHEMA,
    SYNC_ENDPOINT_SCHEMA,
    SYNC_PHASE_SCHEMA,
    SYNC_TRACK_SCHEMA,
)

__all__ = [
    "__version__",
    "CHOREOGRAPHY_SCHEMA",
    "OP_CHOREOGRAPHY_PLAY",
    "OP_SYNC_FOLLOW",
    "SAFE_STATE_OBSERVATION_SCHEMA",
    "SYNC_ENDPOINT_SCHEMA",
    "SYNC_PHASE_SCHEMA",
    "SYNC_TRACK_SCHEMA",
]
