"""Verify the paired prompt-ablation manifest and conservative aggregation."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from run_prompt_ablation import (
    load_manifest,
    matching_resume_config,
    profile_summary,
    run_port_offset,
    unused_attempt_path,
)


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

    def test_each_run_uses_a_disjoint_port_offset(self) -> None:
        """A just-closed prior service cannot block the next paired run."""
        self.assertEqual(
            [run_port_offset(700, 100, index) for index in range(1, 7)],
            [
                700,
                800,
                900,
                1000,
                1100,
                1200,
            ],
        )

    def test_attempt_paths_never_overwrite_prior_evidence(self) -> None:
        """A resume creates a new console filename when the original exists."""
        with TemporaryDirectory() as directory:
            root = Path(directory) / "console.log"
            root.write_text("old", encoding="utf-8")
            self.assertEqual(unused_attempt_path(root).name, "console.resume-01.log")

    def test_resume_rejects_changed_scientific_inputs(self) -> None:
        """Changing model or task inputs is not a valid continuation."""
        frozen = {
            "coherent_commit": "a",
            "manifest_sha256": "b",
            "config_sha256": "c",
            "model": "gpt-6.1-sol",
            "review_model": "gpt-6.1-sol",
            "execution_order": "sequential paired AB/BA/AB",
        }
        matching_resume_config(frozen, dict(frozen))
        changed = dict(frozen, model="different")
        with self.assertRaisesRegex(ValueError, "model"):
            matching_resume_config(frozen, changed)


if __name__ == "__main__":
    unittest.main()
