import unittest

from roboguide import ARCHITECTURE_BASELINE
from roboguide.architecture import CORE_LOOP, PLANES, baseline_summary


class ArchitectureBaselineTests(unittest.TestCase):
    def test_baseline_version(self) -> None:
        self.assertEqual(ARCHITECTURE_BASELINE, "V1.1")

    def test_four_logical_planes_have_package_ownership(self) -> None:
        self.assertEqual(len(PLANES), 4)
        self.assertTrue(all(plane.package.startswith("roboguide.") for plane in PLANES))

    def test_core_loop_is_ordered(self) -> None:
        self.assertEqual(
            CORE_LOOP,
            ("Observe", "Reason", "Schedule", "Coordinate", "Execute", "Reconcile"),
        )

    def test_summary_is_available_without_optional_dependencies(self) -> None:
        summary = baseline_summary()
        self.assertIn("RoboGuide architecture V1.1", summary)
        self.assertIn("Observe -> Reason -> Schedule", summary)


if __name__ == "__main__":
    unittest.main()
