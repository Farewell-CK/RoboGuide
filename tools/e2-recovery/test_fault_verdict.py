"""Verify recovery verdicts for step- and agent-targeted fault injection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_fault import fault_verdict


RECOVERY_EVENTS = (
    "fault_triggered",
    "local_handle_confirmed",
    "primary_exited",
    "recovery_required",
    "recovery_authorized",
    "same_owner_registered",
    "rebind_completed",
)


class FaultVerdictTests(unittest.TestCase):
    """Require the declared fault target to match the observed trigger evidence."""

    def write_evidence(
        self, output: Path, *, target_agent_id: int, trigger_mode: str, steps: int
    ) -> None:
        """Write the minimum machine evidence consumed by ``fault_verdict``."""
        (output / "verdict.json").write_text('{"success": true}', encoding="utf-8")
        timeline = [
            {
                "event": event,
                **(
                    {
                        "target_agent_id": target_agent_id,
                        "trigger_mode": trigger_mode,
                    }
                    if event == "fault_triggered"
                    else {}
                ),
            }
            for event in RECOVERY_EVENTS
        ]
        (output / "fault-timeline.json").write_text(
            json.dumps(timeline), encoding="utf-8"
        )
        (output / "pre-fault-graph.json").write_text(
            json.dumps({"primitive_steps": steps}), encoding="utf-8"
        )

    def test_agent_target_requires_matching_identity(self) -> None:
        """A Dog-A run cannot pass using evidence from another robot identity."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.write_evidence(
                output, target_agent_id=24, trigger_mode="agent_id", steps=2
            )
            self.assertTrue(
                fault_verdict(output, "f1-node-loss", None, 24)["injection_valid"]
            )
            self.assertFalse(
                fault_verdict(output, "f1-node-loss", None, 25)["injection_valid"]
            )

    def test_step_target_preserves_original_validation(self) -> None:
        """The existing fixed-step F1 protocol remains backward compatible."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.write_evidence(
                output, target_agent_id=25, trigger_mode="completed_step", steps=6
            )
            self.assertTrue(
                fault_verdict(output, "f1-node-loss", 6, None)["injection_valid"]
            )
            self.assertFalse(
                fault_verdict(output, "f1-node-loss", 7, None)["injection_valid"]
            )


if __name__ == "__main__":
    unittest.main()
