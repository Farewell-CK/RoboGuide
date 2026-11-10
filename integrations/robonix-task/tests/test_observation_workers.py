"""Regress slow sensor isolation and sampler-before-transport shutdown ordering."""

from __future__ import annotations

import threading
import unittest
from contextlib import ExitStack

from robonix_task.observation_workers import ObservationWorkers


class ObservationWorkersTests(unittest.TestCase):
    """Exercise real worker threads with an explicitly blocked synthetic sensor source."""

    def test_blocked_sensor_does_not_delay_chassis_and_cleanup_joins_before_close(self) -> None:
        """A second chassis read finishes while the sensor blocks, then shutdown drains it."""
        sensor_entered = threading.Event()
        release_sensor = threading.Event()
        second_chassis = threading.Event()
        cleanup_entered = threading.Event()
        cleanup_finished = threading.Event()
        stopping = threading.Event()
        chassis_owners: list[int | None] = []
        lifecycle: list[str] = []
        stack = ExitStack()

        def sensor_sample() -> None:
            """Hold a synthetic slow probe until the test explicitly releases it."""
            sensor_entered.set()
            release_sensor.wait()
            lifecycle.append("sensor-finished")

        def chassis_sample() -> None:
            """Record per-source ownership and expose the second completed sample."""
            chassis_owners.append(threading.current_thread().ident)
            if len(chassis_owners) == 2:
                second_chassis.set()

        def close_source() -> None:
            """Prove resource teardown happens after the sensor read has returned."""
            lifecycle.append("source-closed")

        def cleanup() -> None:
            """Run the same ExitStack ordering used by the deployed Connector."""
            cleanup_entered.set()
            stack.close()
            cleanup_finished.set()

        workers = ObservationWorkers(sensor_sample, chassis_sample, stopping)
        stack.callback(close_source)
        stack.callback(workers.close)
        cleanup_thread = threading.Thread(target=cleanup)
        try:
            workers.start()
            self.assertTrue(sensor_entered.wait(1))
            self.assertTrue(second_chassis.wait(2.5), "slow sensor blocked chassis refresh")
            self.assertEqual(len(set(chassis_owners)), 1)
            self.assertNotEqual(chassis_owners[0], threading.current_thread().ident)
            cleanup_thread.start()
            self.assertTrue(cleanup_entered.wait(1))
            self.assertTrue(stopping.wait(1))
            self.assertFalse(cleanup_finished.is_set())
            self.assertNotIn("source-closed", lifecycle)
        finally:
            release_sensor.set()
            if cleanup_thread.ident is not None:
                cleanup_thread.join(2)
            else:
                stack.close()
        self.assertTrue(cleanup_finished.is_set())
        self.assertEqual(lifecycle, ["sensor-finished", "source-closed"])
        self.assertFalse(cleanup_thread.is_alive())

    def test_disabled_chassis_still_stops_sensor_without_waiting_three_seconds(self) -> None:
        """A sensor-only Connector wakes its cadence wait promptly when shutdown begins."""
        sampled = threading.Event()
        stopping = threading.Event()
        finished = threading.Event()
        workers = ObservationWorkers(sampled.set, None, stopping)

        def close_workers() -> None:
            """Signal only after shutdown has joined the sensor worker."""
            workers.close()
            finished.set()

        cleanup_thread = threading.Thread(target=close_workers)
        workers.start()
        try:
            self.assertTrue(sampled.wait(1))
            cleanup_thread.start()
            self.assertTrue(finished.wait(1), "shutdown did not interrupt the 3s cadence wait")
        finally:
            if cleanup_thread.ident is not None:
                cleanup_thread.join()
            else:
                workers.close()
        self.assertTrue(stopping.is_set())

    def test_shutdown_waits_for_chassis_read_before_closing_transport(self) -> None:
        """An in-flight chassis sample retains its channel until its own worker exits."""
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        stopping = threading.Event()
        lifecycle: list[str] = []

        def chassis_sample() -> None:
            """Hold the synthetic state read across the beginning of shutdown."""
            entered.set()
            release.wait()
            lifecycle.append("chassis-finished")

        def close_source() -> None:
            """Record channel teardown after the sampler cleanup callback."""
            lifecycle.append("transport-closed")

        workers = ObservationWorkers(lambda: None, chassis_sample, stopping)
        stack = ExitStack()
        stack.callback(close_source)
        stack.callback(workers.close)

        def cleanup() -> None:
            """Drain source workers before publishing successful resource teardown."""
            stack.close()
            finished.set()

        cleanup_thread = threading.Thread(target=cleanup)
        try:
            workers.start()
            self.assertTrue(entered.wait(1))
            cleanup_thread.start()
            self.assertTrue(stopping.wait(1))
            self.assertFalse(finished.is_set())
            self.assertNotIn("transport-closed", lifecycle)
        finally:
            release.set()
            if cleanup_thread.ident is not None:
                cleanup_thread.join(2)
            else:
                stack.close()
        self.assertTrue(finished.is_set())
        self.assertEqual(lifecycle, ["chassis-finished", "transport-closed"])
        self.assertFalse(cleanup_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
