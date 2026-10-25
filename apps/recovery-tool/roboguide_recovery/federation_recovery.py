"""Independently join original cross-domain decisions, clocks and actual local Control ownership."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from typing import Any

from .capacity_recovery import _checkpoint, _closed, _integer
from .snapshot_references import canonical

WRAPPER = "roboguide.controller-checkpoint/v60"
SCHEMA = "roboguide.federation-execution/v0.1"


def _typed(value: Any) -> Any:
    """Encode JSON types so portable float bits cannot alias a literal object."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite original Federation identity")
        return ["float64", struct.pack(">d", value).hex()]
    if isinstance(value, list):
        return ["array", [_typed(item) for item in value]]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ["object", [[key, _typed(value[key])] for key in sorted(value)]]
    raise ValueError("original Federation identity is not closed JSON")


def _digest(value: Any) -> str:
    """Match the new portable typed protocol without changing legacy metadata-only identities."""
    return (
        "sha256:"
        + hashlib.sha256(
            b"roboguide.federation-identity/v0.1\0" + canonical(_typed(value))
        ).hexdigest()
    )


def _manifest(value: Any) -> dict[str, Any]:
    """Reject incomplete original global inputs and invented or ambiguous domain ownership."""
    item = _closed(
        value,
        {
            "schema_version",
            "transaction_id",
            "coordinator_domain",
            "tenant_id",
            "objective",
            "budget_ms",
            "max_clock_uncertainty_ms",
            "participants",
        },
    )
    if (
        item["schema_version"] != SCHEMA
        or not isinstance(item["transaction_id"], str)
        or not item["transaction_id"]
        or not item["tenant_id"]
        or not item["objective"]
        or not _integer(item["budget_ms"], 300_000)
        or item["budget_ms"] < 1000
        or not _integer(item["max_clock_uncertainty_ms"], 10_000)
        or item["max_clock_uncertainty_ms"] == 0
        or item["max_clock_uncertainty_ms"] >= item["budget_ms"]
        or not isinstance(item["participants"], dict)
        or not 2 <= len(item["participants"]) <= 16
        or item["coordinator_domain"] not in item["participants"]
    ):
        raise ValueError("original Federation manifest identity or bound differs")
    missions = set()
    for domain, local in item["participants"].items():
        if not isinstance(domain, str) or not domain or len(domain) > 128:
            raise ValueError("original Federation domain is invalid")
        _closed(local, {"plan", "goal_verifier"})
        mission = local["plan"]["mission"]
        if (
            mission["id"] in missions
            or domain == item["coordinator_domain"]
            and mission["objective"] != item["objective"]
        ):
            raise ValueError("global objective or unique local Mission identity changed")
        missions.add(mission["id"])
    return item


def _budget(value: Any, maximum: int = 300_000) -> None:
    """Validate original kernel evidence without renewing its deadline."""
    item = _closed(value, {"issued", "duration_ms"})
    issued = _closed(item["issued"], {"boot_id", "namespace", "elapsed_ms", "resolution_ms"})
    if (
        not _integer(item["duration_ms"], maximum)
        or item["duration_ms"] == 0
        or not isinstance(issued["boot_id"], str)
        or len(issued["boot_id"]) != 36
        or not isinstance(issued["namespace"], list)
        or len(issued["namespace"]) != 2
        or not all(_integer(part) for part in issued["namespace"])
        or issued["namespace"][1] == 0
        or not _integer(issued["elapsed_ms"])
        or issued["resolution_ms"] not in {1, 10, 100}
    ):
        raise ValueError("original Federation kernel budget identity differs")


def _decision(value: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    """Require the exact complete all-domain original normal-Control resource proof set."""
    item = _closed(value, {"manifest_digest", "prepared", "digest"})
    original = {"manifest_digest": item["manifest_digest"], "prepared": item["prepared"]}
    if (
        item["manifest_digest"] != _digest(manifest)
        or item["digest"] != _digest(original)
        or set(item["prepared"]) != set(manifest["participants"])
    ):
        raise ValueError("original immutable global Commit decision changed")
    for domain, receipt in item["prepared"].items():
        if (
            receipt["manifest_digest"] != item["manifest_digest"]
            or receipt["domain"] != domain
            or receipt["phase"] != "prepared"
            or not receipt["bindings"]
        ):
            raise ValueError("original global decision lacks participant resource proof")
    return item


def _remaining(budget: dict[str, Any], sample: dict[str, Any]) -> int:
    """Recompute original decision evidence without reading the current host clock."""
    issued = budget["issued"]
    if (
        issued["boot_id"] != sample["boot_id"]
        or issued["namespace"] != sample["namespace"]
        or sample["elapsed_ms"] < issued["elapsed_ms"]
    ):
        return 0
    return max(
        0,
        int(
            budget["duration_ms"]
            - (sample["elapsed_ms"] - issued["elapsed_ms"])
            - max(issued["resolution_ms"], sample["resolution_ms"])
        ),
    )


def _goal_progress(
    record: dict[str, Any], mission: dict[str, Any], control: dict[str, Any]
) -> None:
    """Require original goal receipts and receive/decision clocks for the exact owner."""
    evidence = record["goal_evidence"]
    receipts = {
        identity: receipt
        for identity, receipt in control["mission_goal_receipts"].items()
        if receipt["evidence"]["mission_id"] == record["mission"]
    }
    if (
        set(evidence) != set(receipts)
        or not record["authorization_registry_id"]
        or not record["authorization_path"]
    ):
        raise ValueError("Federation independent goal lacks original receive or registry authority")
    for identity, progress in evidence.items():
        _closed(progress, {"budget", "completed_time", "incident"})
        _budget(progress["budget"], 60_000)
        receipt = receipts[identity]
        if progress["budget"]["duration_ms"] != receipt["evidence"]["valid_for_ms"]:
            raise ValueError("original Federation goal budget changed")
        if progress["completed_time"] is not None:
            if (
                _remaining(progress["budget"], progress["completed_time"]) == 0
                or mission["lifecycle"] != "Completed"
                or mission["goal_receipt"] != identity
            ):
                raise ValueError(
                    "global contribution completed without original fresh independent goal"
                )
    if (
        mission.get("goal_receipt") is not None
        and evidence.get(mission["goal_receipt"], {}).get("completed_time") is None
    ):
        raise ValueError("completed original contribution lost its independent goal decision clock")


def _participant(
    state: dict[str, Any],
    control: dict[str, Any],
    orchestration: list[dict[str, Any]],
    runtime: dict[str, Any],
    policies: dict[str, Any],
) -> None:
    """Join original probe/resource receipts to local semantic and ownership history."""
    _closed(state, {"probes", "prepared"})
    if len(state["probes"]) > 128 or len(state["prepared"]) > 128:
        raise ValueError("original participant history bound exceeded")
    for identity, probe in state["probes"].items():
        _closed(
            probe,
            {
                "manifest",
                "nonce",
                "principal",
                "leaf",
                "policy_digest",
                "epoch",
                "received_at_ms",
                "budget",
                "receipt",
                "aborted",
            },
        )
        manifest = _manifest(probe["manifest"])
        _budget(probe["budget"])
        matching = [
            policy for policy in policies.values() if _digest(policy) == probe["policy_digest"]
        ]
        if (
            identity != manifest["transaction_id"]
            or not matching
            or len(probe["nonce"]) != 64
            or not probe["epoch"]
            or probe["receipt"]["manifest_digest"] != _digest(manifest)
            or probe["receipt"]["nonce"] != probe["nonce"]
            or probe["receipt"]["epoch"] != probe["epoch"]
            or probe["budget"]["duration_ms"] != manifest["budget_ms"]
        ):
            raise ValueError("original participant probe authority differs")
        policy = matching[0]
        grant = policy["grants"].get(probe["leaf"], {})
        if (
            policy["schema_version"] != "roboguide.federation-authorization/v0.2"
            or not grant.get("execution_coordinator")
            or grant.get("revoked")
            or grant.get("principal_id") != probe["principal"]
            or manifest["tenant_id"] not in grant.get("tenant_ids", [])
            or not set(manifest["participants"]) <= set(grant.get("domain_ids", []))
        ):
            raise ValueError("original resource-bearing coordinator delegation absent")
    for identity, record in state["prepared"].items():
        _closed(
            record,
            {
                "probe_id",
                "goal_evidence",
                "authorization_path",
                "authorization_registry_id",
                "mission",
                "group",
                "bindings",
                "receipt",
                "decision",
                "committed",
                "release",
                "stopped",
            },
        )
        probe = state["probes"].get(identity)
        if probe is None or record["probe_id"] != identity:
            raise ValueError("normal resource preparation lost original probe")
        policy = next(
            policy for policy in policies.values() if _digest(policy) == probe["policy_digest"]
        )
        manifest = probe["manifest"]
        local = manifest["participants"][policy["local_domain"]]
        mission = next(
            (row for row in orchestration if row["plan"]["mission"]["id"] == record["mission"]),
            None,
        )
        if mission is None:
            raise ValueError("original participant Mission is missing")
        if (
            record["mission"] != local["plan"]["mission"]["id"]
            or record["receipt"]["manifest_digest"] != _digest(manifest)
            or record["receipt"]["bindings"] != record["bindings"]
            or not record["bindings"]
            or mission["group_id"] != record["group"]
            or record["release"] is not None
            and record["committed"] is None
        ):
            raise ValueError("original Federation local plan/Group/bindings differ")
        groups = [group for group in control["groups"] if group["group_id"] == record["group"]]
        goals = [
            goal
            for goal in control["mission_goal_contracts"].values()
            if goal["mission_id"] == record["mission"]
        ]
        if len(groups) != 1 or len(goals) != 1 or goals[0]["context_digest"] != _digest(manifest):
            raise ValueError("original Federation goal or Control Group missing")
        _goal_progress(record, mission, control)
        tasks = {
            json.dumps(pair[0], sort_keys=True): pair[1] for pair in groups[0]["task_executions"]
        }
        for binding in record["bindings"]:
            _closed(binding, {"task_ref", "assignment", "grants"})
            task = tasks.get(json.dumps(binding["task_ref"], sort_keys=True))
            if task is None or binding["task_ref"]["mission_id"] != record["mission"]:
                raise ValueError("original Federation binding Task missing")
            if (
                record["release"] is None
                and record["stopped"] is None
                and binding["assignment"] not in task["assignments"]
            ):
                raise ValueError("prepared Federation resource ownership differs")
        if record["decision"] is not None:
            decision = _decision(record["decision"], manifest)
            if (
                decision["prepared"].get(policy["local_domain"]) != record["receipt"]
                or record["committed"] is None
            ):
                raise ValueError("original local Commit lost exact global decision")
            if record["committed"]["decision_digest"] != decision["digest"] or record["committed"][
                "prepared_digest"
            ] != _digest(record["receipt"]):
                raise ValueError("original local Commit receipt changed")
        elif record["committed"] is not None:
            raise ValueError("local Commit invented a global decision")
        if record["release"] is not None:
            if (
                set(record["release"]) != set(manifest["participants"])
                or record["release"][policy["local_domain"]] != record["committed"]
            ):
                raise ValueError("release lost complete original Commit receipts")
            for domain, receipt in record["release"].items():
                if (
                    receipt["domain"] != domain
                    or receipt["phase"] != "committed"
                    or receipt["decision_digest"] != record["decision"]["digest"]
                ):
                    raise ValueError("release contains unrelated original Commit proof")


def _profiles(profiles: dict[str, Any]) -> None:
    """Check original peer authority without replacing historical credentials."""
    previous = None
    for index, (key, profile) in enumerate(
        sorted(profiles.items(), key=lambda item: int(item[0])), 1
    ):
        if not isinstance(profile, dict):
            raise ValueError("original peer profile is not an object")
        modern = profile.get("schema_version") == "roboguide.federation-peers/v0.2"
        _closed(
            profile,
            {
                "schema_version",
                "profile_id",
                "revision",
                "local_domain",
                "max_drift_ppm",
                "request_budget_ms",
                "peers",
            }
            | ({"budget_scope"} if modern else set()),
        )
        if (
            str(index) != key
            or profile["revision"] != index
            or profile["schema_version"]
            not in {"roboguide.federation-peers/v0.1", "roboguide.federation-peers/v0.2"}
            or (modern and profile["budget_scope"] != "intent-publication-and-peer-io")
            or not profile["profile_id"]
            or not _integer(profile["max_drift_ppm"], 1000)
            or not _integer(profile["request_budget_ms"], 30_000 if modern else 10_000)
            or profile["request_budget_ms"] < 100
            or not 2 <= len(profile["peers"]) <= 16
            or profile["local_domain"] not in profile["peers"]
        ):
            raise ValueError("original peer profile identity, budget or revision differs")
        if previous is not None and (
            (previous["schema_version"] == "roboguide.federation-peers/v0.2" and not modern)
            or profile["profile_id"] != previous["profile_id"]
            or profile["local_domain"] != previous["local_domain"]
            or not set(previous["peers"]) <= set(profile["peers"])
        ):
            raise ValueError("original peer profile rollback or domain removal")
        for domain, peer in profile["peers"].items():
            _closed(
                peer,
                {
                    "address",
                    "server_name",
                    "server_leaf",
                    "ca_file",
                    "ca_digest",
                    "client_cert",
                    "client_leaf",
                    "client_key",
                    "principal_id",
                },
            )
            if any(not isinstance(value, str) or not value for value in peer.values()):
                raise ValueError("original peer endpoint or credential reference missing")
            for key in ("server_leaf", "client_leaf", "ca_digest"):
                value = peer[key]
                if (
                    len(value) != 71
                    or not value.startswith("sha256:")
                    or any(character not in "0123456789abcdef" for character in value[7:])
                ):
                    raise ValueError("original peer actual identity digest differs")
            if (
                previous is not None
                and domain in previous["peers"]
                and peer["principal_id"] != previous["peers"][domain]["principal_id"]
            ):
                raise ValueError("original peer stable principal changed")
        previous = profile


def _clocks(record: dict[str, Any], profiles: dict[str, Any]) -> None:
    """Recompute original offset intervals and budgets from retained actual samples."""
    if set(record["clocks"]) != set(record["probes"]):
        raise ValueError("consumed actual probes lack their original clock translations")
    manifest = record["manifest"]
    for domain, proof in record["clocks"].items():
        intent = record["intents"][domain]["probe"]
        probe = record["probes"][domain]
        profile = profiles[str(intent["profile_revision"])]
        sample = intent["received_kernel"]
        if sample is None or intent["received_at_ms"] is None:
            raise ValueError("clock translation lost its actual local receive evidence")
        first, last = probe["received_at_ms"], probe["response_at_ms"]
        received = intent["received_at_ms"]
        sent = intent["sent_at_ms"]
        drift = (manifest["budget_ms"] * profile["max_drift_ppm"] + 999_999) // 1_000_000 + 20
        expected = {
            "sent_at_ms": sent,
            "received_at_ms": received,
            "remote_received_at_ms": first,
            "remote_response_at_ms": last,
            "minimum_offset_ms": last - received - drift,
            "maximum_offset_ms": first - sent + drift,
            "remaining_at_receive_ms": min(
                _remaining(record["budget"], sample),
                max(0, manifest["budget_ms"] - (received - sent) - drift),
            ),
        }
        if (
            proof != expected
            or sent > received
            or first > last
            or probe["nonce"] != record["nonces"][domain]
            or probe["manifest_digest"] != _digest(manifest)
            or probe["domain"] != domain
            or probe["transaction_id"] != manifest["transaction_id"]
            or probe["duration_ms"] != manifest["budget_ms"]
        ):
            raise ValueError("original peer clock proof changed its actual request/reply identity")


def _coordinator(state: dict[str, Any]) -> None:
    """Reconstruct original outgoing command identities and irreversible root decisions."""
    _closed(state, {"profiles", "transactions"})
    if len(state["profiles"]) > 1024 or len(state["transactions"]) > 128:
        raise ValueError("original Federation coordinator history bound exceeded")
    _profiles(state["profiles"])
    for identity, record in state["transactions"].items():
        _closed(
            record,
            {
                "manifest",
                "root_epoch",
                "principal",
                "leaf",
                "certificate_expires_at_ms",
                "budget",
                "nonces",
                "probes",
                "clocks",
                "prepared",
                "decision",
                "commits",
                "released",
                "intents",
                "completed",
                "stopping",
                "cleanup_budget",
                "stopped",
            },
        )
        manifest = _manifest(record["manifest"])
        _budget(record["budget"])
        _clocks(record, state["profiles"])
        if record["cleanup_budget"] is not None:
            _budget(record["cleanup_budget"], 30_000)
        if (
            identity != manifest["transaction_id"]
            or set(record["nonces"]) != set(manifest["participants"])
            or record["budget"]["duration_ms"] != manifest["budget_ms"]
        ):
            raise ValueError("original root identity/nonces/budget differ")
        if record["decision"] is not None:
            decision = _decision(record["decision"], manifest)
            if decision["prepared"] != record["prepared"]:
                raise ValueError("root decision replaced original prepared proofs")
        if not set(record["released"]) <= set(record["commits"]) or not set(
            record["completed"]
        ) <= set(record["released"]):
            raise ValueError("original root phase coverage differs")
        for domain, phases in record["intents"].items():
            if domain not in manifest["participants"] or len(phases) > 7:
                raise ValueError("original root peer domain or phase differs")
            for phase, intent in phases.items():
                _closed(
                    intent,
                    {
                        "request_digest",
                        "sent_at_ms",
                        "budget",
                        "profile_revision",
                        "reply",
                        "received_at_ms",
                        "received_kernel",
                        "error",
                        "observations",
                    },
                )
                _budget(intent["budget"], 10_000)
                if (
                    str(intent["profile_revision"]) not in state["profiles"]
                    or len(intent["observations"]) > 16
                ):
                    raise ValueError("original root peer profile or observation bound differs")
                wire = (
                    None
                    if phase == "observe"
                    else {
                        "schema_version": SCHEMA,
                        "manifest": manifest,
                        "nonce": record["nonces"][domain],
                        "epoch": None if phase == "probe" else record["probes"][domain]["epoch"],
                        "decision": record["decision"] if phase in {"commit", "release"} else None,
                        "commits": record["commits"] if phase == "release" else None,
                    }
                )
                if intent["request_digest"] != _digest(wire):
                    raise ValueError("original outgoing Federation command bytes changed")
                for observation in intent["observations"]:
                    _closed(observation, {"budget", "sent_at_ms", "reply", "error"})
                    _budget(observation["budget"], 10_000)
        for domain, proof in record["clocks"].items():
            _closed(
                proof,
                {
                    "sent_at_ms",
                    "received_at_ms",
                    "remote_received_at_ms",
                    "remote_response_at_ms",
                    "minimum_offset_ms",
                    "maximum_offset_ms",
                    "remaining_at_receive_ms",
                },
            )
            if (
                domain not in record["probes"]
                or proof["received_at_ms"] < proof["sent_at_ms"]
                or proof["maximum_offset_ms"] < proof["minimum_offset_ms"]
                or proof["maximum_offset_ms"] - proof["minimum_offset_ms"]
                > manifest["max_clock_uncertainty_ms"]
            ):
                raise ValueError("original peer clock translation is unavailable or regressed")


def validate(connection: sqlite3.Connection) -> None:
    """Refuse hidden new authority or missing v60 tables before publishing immutable bytes."""
    wrapper = _checkpoint(
        connection,
        schemas=frozenset(
            f"roboguide.controller-checkpoint/v{version}" for version in range(49, 61)
        ),
    )
    if wrapper is None:
        return
    federation = wrapper.get("federation", {})
    if wrapper["schema"] != WRAPPER:
        if (
            any(federation.get("executions", {}).values())
            or any(federation.get("coordinations", {}).values())
            or any(
                policy.get("schema_version") == "roboguide.federation-authorization/v0.2"
                for policy in federation.get("policies", {}).values()
            )
        ):
            raise ValueError("old Controller wrapper hides resource-bearing Federation authority")
        return
    if not {"executions", "coordinations"} <= set(federation):
        raise ValueError("current Federation original reference tables missing")
    integration = json.loads(wrapper["integration_json"])
    orchestration = json.loads(wrapper["orchestration_json"])
    _participant(
        federation["executions"],
        integration["control"],
        orchestration,
        integration["runtime"],
        federation["policies"],
    )
    _coordinator(federation["coordinations"])
