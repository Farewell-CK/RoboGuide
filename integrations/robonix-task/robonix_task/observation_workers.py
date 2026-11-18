"""Isolate fixed chassis sampling from slower sensor probes within one Connector."""

from __future__ import annotations

import threading
from collections.abc import Callable


class ObservationWorkers:
    """Own serial per-source samplers and join them before their transports close."""

    def __init__(
        self,
        sensor_sample: Callable[[], None],
        chassis_sample: Callable[[], None] | None,
        stopping: threading.Event,
    ) -> None:
        """Keep the sensor cadence and give the optional chassis source its own worker."""
        self._stopping = stopping
        self._started: list[threading.Thread] = []
        self._threads = [
            threading.Thread(
                target=self._observe,
                args=(sensor_sample, 3.0),
                name="robonix-observer",
            )
        ]
        if chassis_sample is not None:
            self._threads.append(
                threading.Thread(
                    target=self._observe,
                    args=(chassis_sample, 1.0),
                    name="robonix-chassis-observer",
                )
            )

    def start(self) -> None:
        """Start each sampler once and drain already-started workers if startup fails."""
        try:
            for worker in self._threads:
                worker.start()
                self._started.append(worker)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Wake waiting samplers and join every in-flight read before transport cleanup."""
        self._stopping.set()
        for worker in self._started:
            worker.join()

    def _observe(self, sample: Callable[[], None], interval_seconds: float) -> None:
        """Sample one source serially; source-owned caches retain original freshness."""
        while not self._stopping.is_set():
            sample()
            self._stopping.wait(interval_seconds)
