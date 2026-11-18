"""Verify exact offline operator closure without rewriting or replaying native work."""

import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from robonix_task.journal import Conflict, Journal
from robonix_task.manual_settlement import audit_digest, settle
from robonix_task.protocol import event_dict, message
from test_protocol import request


class ManualSettlementTests(unittest.TestCase):
    """Use real journal locking, original protobuf bytes and cold-open validation."""

    def prepare(self, root):
        """Retain a cancelled Unknown with an actual motion-plan event, not a terminal task."""
        journal = Journal(root, {"fixture": "operator-settlement"})
        body = request()
        body["invocation"]["execution_generation"] = 1
        row, _ = journal.admit(body, lambda value: None)
        handle = row["handle"]
        journal.begin(handle)
        provider = {"provider_id": "fixture", "registration_id": "original"}
        journal.bind_provider(handle, provider)
        event = message(
            "robonix.pilot.PilotEvent",
            event_kind=1,
            session_id=row["session_id"],
            plan={"plan_id": "original-plan", "session_id": row["session_id"]},
        )
        wire = event.SerializeToString()
        journal.append(handle, "task", wire, event_dict(wire), provider)
        journal.cancel(handle)
        journal.begin_abort(handle)
        journal.mark_unknown(handle, "original task lost after native restart")
        row = journal.get(handle)
        command = {
            "schema_version": "robonix.local-cancel-settlement/v0.1",
            "command_id": "reviewed-cancel-1",
            "handle": handle,
            "request_digest": row["digest"],
            "expected_sequence": row["sequence"],
            "expected_audit_sha256": audit_digest(journal, handle),
            "confirmed": True,
            "disposition": "stopped",
            "attestation": "Fixture operator separately reviewed native idle and device state.",
            "evidence": {"review": "synthetic field inspection, not hardware proof"},
        }
        return journal, row, command

    def test_closure_preserves_history_and_never_exports_no_effects(self):
        """Release only the local slot, retain original bytes, and replay the exact receipt."""
        with tempfile.TemporaryDirectory() as tmp:
            journal, before, command = self.prepare(tmp)
            events = journal.events(before["handle"])
            receipt = settle(journal, command, "local-os:fixture:operator")
            self.assertFalse(receipt["physical_stop_confirmed"])
            self.assertFalse(receipt["automatic_continuation_authorized"])
            after = journal.get(before["handle"])
            self.assertEqual(after["state"], "CANCELED")
            for key in ("request", "digest", "created_ns", "session_id", "task_id", "abort_id"):
                self.assertEqual(after[key], before[key])
            self.assertEqual(journal.events(before["handle"]), events)
            self.assertEqual(receipt["previous_detail"], before["detail"])
            self.assertEqual(journal.no_effects_observations(), {"observations": []})
            self.assertEqual(settle(journal, command, "local-os:fixture:operator"), receipt)
            self.assertEqual(journal.get(before["handle"])["sequence"], after["sequence"])
            journal.close()
            journal = Journal(tmp, {"fixture": "operator-settlement"})
            self.assertEqual(settle(journal, command, "local-os:fixture:operator"), receipt)
            _, created = journal.admit(request("new-attempt"), lambda value: None)
            self.assertTrue(created)
            journal.close()

    def test_conflicts_and_invalid_commands_leave_original_unknown(self):
        """Reject stale, cross-task, missing-confirmation and ambiguous disposition commands."""
        for change in (
            {"expected_sequence": 999},
            {"request_digest": "0" * 64},
            {"expected_audit_sha256": "0" * 64},
            {"handle": "another-handle"},
            {"confirmed": False},
            {"expected_sequence": True},
            {"disposition": "no-effects"},
            {"evidence": {}},
            {"unrecognized": True},
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                journal, before, command = self.prepare(tmp)
                command.update(change)
                with self.assertRaises((ValueError, KeyError)):
                    settle(journal, command, "local-os:fixture:operator")
                self.assertEqual(journal.get(before["handle"]), before)
                profile = json.loads(
                    journal.db.execute("SELECT value FROM meta WHERE key='profile'").fetchone()[0]
                )
                self.assertEqual(profile["schema"], "lab-pilot-journal/1")
                journal.close()

    def test_one_owner_and_conflicting_operator_cannot_replace_receipt(self):
        """Require the real OS journal lock and refuse changed principal or command replay."""
        with tempfile.TemporaryDirectory() as tmp:
            journal, _, command = self.prepare(tmp)
            with self.assertRaisesRegex(Conflict, "another process"):
                Journal(tmp, {"fixture": "operator-settlement"})
            settle(journal, command, "local-os:fixture:operator")
            for principal, changed in (
                ("local-os:fixture:other", command),
                (
                    "local-os:fixture:operator",
                    {**command, "attestation": "Different reviewed basis."},
                ),
                ("local-os:fixture:operator", {**command, "command_id": "different"}),
            ):
                with self.assertRaises(Conflict):
                    settle(journal, changed, principal)
            journal.close()

    def test_cold_restore_rejects_tampered_or_missing_original_evidence(self):
        """Detect altered native bytes, original input, operator receipt and missing markers."""
        for mutation in (
            "UPDATE events SET raw=x'00'",
            "UPDATE runs SET created_ns=created_ns+1",
            "UPDATE runs SET detail='changed'",
            "DELETE FROM operator_settlements",
            "UPDATE operator_settlements SET principal='someone-else'",
            "UPDATE meta SET value=replace(value,'lab-pilot-journal/2','lab-pilot-journal/1')",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                journal, _, command = self.prepare(tmp)
                settle(journal, command, "local-os:fixture:operator")
                journal.close()
                with closing(sqlite3.connect(Path(tmp) / "pilot.sqlite3")) as db, db:
                    db.execute(mutation)
                with self.assertRaises(Conflict):
                    Journal(tmp, {"fixture": "operator-settlement"})

    def test_new_native_event_invalidates_reviewed_snapshot(self):
        """A new observation between review and settlement must require a new review."""
        with tempfile.TemporaryDirectory() as tmp:
            journal, before, command = self.prepare(tmp)
            journal.cancel(before["handle"])
            changed = copy.deepcopy(command)
            changed["expected_sequence"] = journal.get(before["handle"])["sequence"]
            with self.assertRaises(Conflict):
                settle(journal, changed, "local-os:fixture:operator")
            journal.close()


if __name__ == "__main__":
    unittest.main()
