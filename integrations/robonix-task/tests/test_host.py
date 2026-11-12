"""Check compute-host enrollment facts without claiming that powered-off hardware is ready."""

import tempfile
import unittest
from pathlib import Path

from robonix_task.host import inspect_host


class HostTests(unittest.TestCase):
    """Exercise actual bounded local source reads and missing-hardware behavior."""

    def test_host_available_with_no_robot_measurements(self):
        """Missing robot observations remain missing while the host observation completes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = inspect_host("lite3-dog1", {}, proc_root=root, sys_root=root)
            self.assertEqual(facts["execution_state"], "SUCCEEDED")
            self.assertEqual(facts["health_state"], "ONLINE")
            self.assertIsNone(facts["host"]["boot_id"])
            self.assertIsNone(facts["robot_observation"]["sensors"])
            self.assertEqual(facts["chassis_power"], "not_observed")
            self.assertIs(facts["motion_authorized"], False)

    def test_original_robot_availability_is_preserved_and_detached(self):
        """Host completion cannot refresh or promote the original robot evidence."""
        observations = {
            "mapping": {"availability": "unavailable", "value": None},
            "sensors": {"battery": {"availability": "unavailable", "value": None}},
            "navigation_readiness": {"state": "UNAVAILABLE"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uptime").write_text("100.25 42.0")
            facts = inspect_host("lite3-dog2", observations, proc_root=root, sys_root=root)
            self.assertEqual(facts["robot_observation"]["mapping"], observations["mapping"])
            facts["robot_observation"]["mapping"]["availability"] = "changed"
            self.assertEqual(observations["mapping"]["availability"], "unavailable")
            self.assertEqual(facts["host"]["uptime_seconds"], 100.25)

    def test_hardware_and_carrier_are_observations_not_power_proof(self):
        """Carrier-down and missing measurements cannot infer a powered-off chassis."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interface = root / "class/net/ethernet"
            interface.mkdir(parents=True)
            (interface / "carrier").write_text("0\n")
            (interface / "operstate").write_text("down\n")
            (root / "device-tree").mkdir()
            (root / "device-tree/model").write_bytes(b"NVIDIA Jetson AGX Thor\x00")
            (root / "uptime").write_text("nan 0")
            facts = inspect_host("lite3-dog1", {}, proc_root=root, sys_root=root)
            self.assertEqual(facts["host"]["hardware_model"], "NVIDIA Jetson AGX Thor")
            self.assertIsNone(facts["host"]["uptime_seconds"])
            self.assertEqual(facts["host"]["interfaces"][0]["carrier"], False)
            self.assertEqual(facts["chassis_power"], "not_observed")


if __name__ == "__main__":
    unittest.main()
