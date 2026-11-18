"""Exercise the freshness and source fences of the Controller-only position reader."""

import copy
import ssl
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from robonix_task.position import (
    MAX_RESPONSE_BYTES,
    annotate_mapping_age,
    inspect_position,
    main,
    read_controller,
)


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build independently timed Controller and publisher evidence for one exact source."""
    return (
        {
            "schema": "roboguide.state-query/v0.1",
            "observed_at_ms": 100_000,
            "records": [
                {
                    "object": {"class": "node", "object_type": "robot", "object_id": "dog-2"},
                    "semantic": "reported",
                    "source": "node:dog-2/robot-observer",
                    "channel_id": "robot-state",
                    "stale": False,
                    "value": {
                        "payload_schema": "lite3.robot-state/v0.1",
                        "received_at_ms": 99_000,
                        "valid_for_ms": 10_000,
                        "source_epoch": "actual-node-session",
                        "sequence": 7,
                        "value": {
                            "mapping": {
                                "availability": "current",
                                "age_ms": 2000,
                                "value": {
                                    "has_map": True,
                                    "mode": "localization",
                                    "map_id": "current-map",
                                    "pose": {"x": 1.25, "y": -2.5, "theta": 0.4},
                                },
                            }
                        },
                    },
                }
            ],
        },
        {
            "schema_version": "roboguide.inventory/v0.1",
            "nodes": [{"node_id": "dog-2", "liveness": "Reachable"}],
        },
    )


def inspect(state: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Apply explicit deployment identity and map requirements to the original responses."""
    return inspect_position(state, inventory, node_id="dog-2", expected_map_id="current-map")


class PositionTests(unittest.TestCase):
    """Keep diagnostic reads separate from motion, grounding and localization authority."""

    def test_reported_coordinates_preserve_evidence_and_limitations(self) -> None:
        """Return coordinates with unresolved localization and map qualifications explicit."""
        state, inventory = fixture()
        original = copy.deepcopy(state)
        result = inspect(state, inventory)
        self.assertEqual(result["status"], "reported")
        self.assertEqual(result["position"]["x_m"], 1.25)
        self.assertEqual(result["evidence"]["mapping_age_plus_controller_retention_ms"], 3000)
        self.assertIsNone(result["position"]["map_revision_id"])
        for key in ["motion_authorized", "localization_verified", "mi_grounding_admitted"]:
            self.assertIs(result[key], False)
        self.assertEqual(state, original)

    def test_fresh_outer_record_cannot_rejuvenate_old_mapping(self) -> None:
        """Reject old cached Mapping data even when Node keeps publishing new envelopes."""
        state, inventory = fixture()
        envelope = state["records"][0]["value"]
        envelope["received_at_ms"] = state["observed_at_ms"]
        envelope["value"]["mapping"]["age_ms"] = 10_001
        result = inspect(state, inventory)
        self.assertEqual(result["reason"], "mapping_response_stale")
        self.assertIsNone(result["position"])

    def test_controller_retention_consumes_original_age(self) -> None:
        """Include same-Controller retention without comparing publisher and Controller clocks."""
        state, inventory = fixture()
        state["records"][0]["value"]["value"]["mapping"]["age_ms"] = 9500
        self.assertEqual(inspect(state, inventory)["reason"], "mapping_response_stale")

    def test_expired_envelope_or_clock_rollback_is_unavailable(self) -> None:
        """Reject stale flags, independently expired TTLs and impossible Controller ordering."""
        for kind in ["flag", "ttl", "rollback"]:
            with self.subTest(kind=kind):
                state, inventory = fixture()
                if kind == "flag":
                    state["records"][0]["stale"] = True
                else:
                    state["observed_at_ms"] = 109_000 if kind == "ttl" else 98_999
                self.assertEqual(
                    inspect(state, inventory)["reason"], "controller_record_stale_or_clock_invalid"
                )

    def test_map_mismatch_and_missing_localization_do_not_return_coordinates(self) -> None:
        """Keep a different map or mapping-mode pose out of this localization inspection."""
        for field, value, reason in [
            ("map_id", "other-map", "map_mismatch"),
            ("mode", "mapping", "localization_mode_not_reported"),
            ("has_map", False, "active_map_unavailable"),
        ]:
            with self.subTest(field=field):
                state, inventory = fixture()
                state["records"][0]["value"]["value"]["mapping"]["value"][field] = value
                result = inspect(state, inventory)
                self.assertEqual(result["reason"], reason)
                self.assertIsNone(result["position"])

    def test_nonfinite_boolean_and_missing_pose_are_rejected(self) -> None:
        """Do not serialize invalid coordinates or mistake a JSON boolean for a distance."""
        for invalid in [float("nan"), float("inf"), True, None, 10**1000]:
            with self.subTest(value=type(invalid).__name__):
                state, inventory = fixture()
                state["records"][0]["value"]["value"]["mapping"]["value"]["pose"]["x"] = invalid
                self.assertEqual(inspect(state, inventory)["reason"], "finite_map_pose_missing")

    def test_unreachable_node_and_foreign_or_duplicate_sources_are_rejected(self) -> None:
        """Require the actual selected node and one exact nonambiguous registered source."""
        for kind in ["offline", "foreign", "duplicate", "world"]:
            with self.subTest(kind=kind):
                state, inventory = fixture()
                if kind == "offline":
                    inventory["nodes"][0]["liveness"] = "Unreachable"
                elif kind == "foreign":
                    state["records"][0]["source"] = "node:dog-1/robot-observer"
                elif kind == "duplicate":
                    state["records"].append(copy.deepcopy(state["records"][0]))
                else:
                    state["records"][0]["object"]["class"] = "world"
                self.assertEqual(inspect(state, inventory)["status"], "unavailable")

    def test_legacy_age_and_source_identity_gaps_remain_unknown(self) -> None:
        """Existing exports without freshness or original session evidence gain no qualification."""
        for kind in ["age", "epoch", "sequence"]:
            with self.subTest(kind=kind):
                state, inventory = fixture()
                envelope = state["records"][0]["value"]
                if kind == "age":
                    del envelope["value"]["mapping"]["age_ms"]
                else:
                    del envelope["source_epoch" if kind == "epoch" else "sequence"]
                self.assertEqual(inspect(state, inventory)["status"], "unavailable")

    def test_mapping_poll_does_not_reset_source_receipt(self) -> None:
        """Repeated reads age a frozen Mapping response rather than refreshing its timestamp."""
        original = {"mapping": {"received_at_ns": 1_000_000_000, "value": {"pose": {"x": 2}}}}
        early = annotate_mapping_age(original, 2_000_000_000)
        late = annotate_mapping_age(original, 12_000_000_000)
        self.assertEqual(early["mapping"]["availability"], "current")
        self.assertEqual(late["mapping"]["availability"], "stale")
        self.assertEqual(late["mapping"]["received_at_ns"], 1_000_000_000)
        self.assertNotIn("age_ms", original["mapping"])

    def test_missing_receipt_rollback_and_mapping_errors_stay_unavailable(self) -> None:
        """Withhold current labels on absent responses or a source-clock rollback."""
        for original in [{}, {"mapping": {"value": None, "error": "timeout"}}]:
            self.assertEqual(
                annotate_mapping_age(original, 5_000_000_000)["mapping"]["availability"],
                "unavailable",
            )
        result = annotate_mapping_age(
            {"mapping": {"received_at_ns": 7_000_000_000, "value": {}}}, 5_000_000_000
        )
        self.assertEqual(result["mapping"]["availability"], "unavailable")


class ControllerReadTests(unittest.TestCase):
    """Verify the maintained reader performs only bounded GETs against explicit HTTPS."""

    def test_cli_filters_source_locally_without_encoded_controller_query(self) -> None:
        """Keep exact source checks when deployed Controller query parsing is literal-only."""
        state, inventory = fixture()
        arguments = [
            "position",
            "--controller-origin",
            "https://controller:8080",
            "--ca",
            "ca.crt",
            "--cert",
            "reader.crt",
            "--key",
            "reader.key",
            "--node-id",
            "dog-2",
            "--map-id",
            "current-map",
        ]
        with (
            patch("sys.argv", arguments),
            patch("robonix_task.position.ssl.create_default_context"),
            patch("robonix_task.position.read_controller", side_effect=[inventory, state]) as read,
            patch("builtins.print"),
            self.assertRaises(SystemExit) as exited,
        ):
            main()
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(read.call_args_list[0].args[1], "/v1/inventory")
        self.assertEqual(
            read.call_args_list[1].args[1], "/v1/state/records?object_class=node&object_type=robot"
        )

    def test_get_only_disables_proxy_and_redirects(self) -> None:
        """Read a real-shaped response without forwarding an action or client-controlled body."""
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"schema":"test"}'
        opener = MagicMock()
        opener.open.return_value = response
        context = ssl.create_default_context()
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(
                read_controller("https://controller:8080", "/v1/inventory", context),
                {"schema": "test"},
            )
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            response.__enter__.return_value.read.call_args.args[0], MAX_RESPONSE_BYTES + 1
        )

    def test_invalid_origins_fail_before_network_io(self) -> None:
        """Refuse cleartext, userinfo and origins that could substitute another path."""
        for origin in [
            "http://host",
            "https://user@host",
            "https://host/path",
            "https://host/?x=1",
        ]:
            with self.subTest(origin=origin), patch("urllib.request.build_opener") as build:
                with self.assertRaises(ValueError):
                    read_controller(origin, "/v1/inventory", ssl.create_default_context())
                build.assert_not_called()

    def test_large_nonobject_and_nonfinite_responses_fail(self) -> None:
        """Bound Controller response memory and reject unsupported JSON before interpretation."""
        for raw in [b" " * (MAX_RESPONSE_BYTES + 1), b"[]", b'{"value":NaN}']:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = raw
            opener = MagicMock()
            opener.open.return_value = response
            with (
                self.subTest(size=len(raw)),
                patch("urllib.request.build_opener", return_value=opener),
            ):
                with self.assertRaises(ValueError):
                    read_controller("https://host", "/v1/inventory", ssl.create_default_context())


if __name__ == "__main__":
    unittest.main()
