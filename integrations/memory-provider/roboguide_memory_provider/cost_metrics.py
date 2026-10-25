"""Read actual host load without inventing queue, route, traffic or GPU measurements."""

from __future__ import annotations

import math
import os


def scheduling_metrics() -> dict[str, object]:
    """Return host one-minute load per reported logical CPU, with unavailable fields explicit.

    This is a planning observation of the provider's actual host, not CPU utilization, a worker
    resource grant, a cgroup measurement, or hardware enforcement. Unsupported OS observations
    remain null. No command, subprocess, external service or hardware discovery is performed.
    """
    load: int | None = None
    try:
        cpus = os.sysconf("SC_NPROCESSORS_ONLN")
        observed = os.getloadavg()[0]
        if cpus is not None and cpus > 0 and math.isfinite(observed) and observed >= 0:
            load = int(observed * 1_000_000 / cpus)
    except (OSError, AttributeError, ValueError):
        pass
    return {
        "schema_version": "roboguide.scheduling-metrics/v0.1",
        "queue_delay_ms": None,
        "travel_distance_um": None,
        "traffic_delay_ms": None,
        "load_millionths": load,
        "gpu_memory_bytes": None,
    }
