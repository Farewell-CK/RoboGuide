"""Verify the paired prompt-ablation manifest and conservative aggregation."""

from __future__ import annotations

import unittest
from pathlib import Path

from run_prompt_ablation import load_manifest, profile_summary


class PromptAblationTests(unittest.TestCase):
    """Keep profile pairing and failure denominators explicit."""

    def test_frozen_manifest_has_three_exact_pairs(self) -> None:
        """The committed pilot runs each task once under each prompt profile."""
        manifest = load_manifest(Path(__file__).with_name("prompt-ablation-pilot3.json"))
        identities = {(run["env"], run["task"]) for run in manifest["runs"]}
        self.assertEqual(len(identities), 3)

    def test_missing_verdict_remains_in_success_rate_denominator(self) -> None:
        """Infrastructure loss cannot silently disappear from the scheduled-run count."""
        records = [
            {
                "public_task": "env0/task0",
                "prompt_profile": "fair",
                "return_code": 0,
                "verdict": {
                    "success": True,
                    "failure_reason": None,
                    "primitive_steps": 1,
                    "planning_segments": 1,
                    "model_calls": 2,
                    "wall_time_seconds": 3.0,
                },
            },
            {
                "public_task": "env0/task1",
                "prompt_profile": "fair",
                "return_code": 1,
                "verdict": None,
            },
        ]
        summary = profile_summary(records, "fair")
        self.assertEqual(summary["machine_verdicts"], 1)
        self.assertEqual(summary["success_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
