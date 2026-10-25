"""Synthetic original Federation SQL counterexamples establish no deployment or device facts."""

from __future__ import annotations

import copy
import json
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from roboguide_recovery.federation_recovery import WRAPPER, _coordinator, _digest, validate
from test_capacity_recovery import database, wrapper


def evidence() -> dict[str, Any]:
    """Build only empty original authority tables under the explicit new wrapper."""
    value = wrapper()
    value["schema"] = WRAPPER
    value["federation"] = {
        "policies": {},
        "records": {},
        "executions": {"probes": {}, "prepared": {}},
        "coordinations": {"profiles": {}, "transactions": {}},
    }
    value["orchestration_json"] = "[]"
    inner = json.loads(value["integration_json"])
    inner["runtime"] = {}
    value["integration_json"] = json.dumps(inner)
    return value


def test_complete_empty_authority_is_readonly(tmp_path: Path) -> None:
    """An empty declared deployment needs no invented participants or devices."""
    with closing(database(tmp_path / "empty.sqlite3", evidence())) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (WRAPPER,))
        sql.commit()
        before = list(sql.iterdump())
        validate(sql)
        assert list(sql.iterdump()) == before


@pytest.mark.parametrize(
    "damage",
    [
        "missing-executions",
        "missing-coordinations",
        "hidden-probe",
        "hidden-profile",
        "hidden-grant",
    ],
)
def test_hidden_or_missing_original_tables_refuse(tmp_path: Path, damage: str) -> None:
    """Backup must reject missing mandatory tables and authority hidden from genuine old writers."""
    value = evidence()
    federation = value["federation"]
    if damage.startswith("missing-"):
        del federation[damage.removeprefix("missing-")]
    else:
        value["schema"] = "roboguide.controller-checkpoint/v59"
        if damage == "hidden-probe":
            federation["executions"]["probes"]["unit"] = {}
        elif damage == "hidden-profile":
            federation["coordinations"]["profiles"]["1"] = {}
        else:
            federation["policies"]["1"] = {
                "schema_version": "roboguide.federation-authorization/v0.2"
            }
    with closing(database(tmp_path / (damage + ".sqlite3"), value)) as sql:
        sql.execute("UPDATE controller_checkpoint SET schema=?", (value["schema"],))
        sql.commit()
        before = list(sql.iterdump())
        with pytest.raises(ValueError):
            validate(sql)
        assert list(sql.iterdump()) == before


def original_intent() -> dict[str, Any]:
    """Retain a labelled synthetic unresolved first probe; no real peer or resource is asserted."""
    manifest = {
        "schema_version": "roboguide.federation-execution/v0.1",
        "transaction_id": "unit-original",
        "coordinator_domain": "unit-a",
        "tenant_id": "unit",
        "objective": "unit original goal",
        "budget_ms": 5000,
        "max_clock_uncertainty_ms": 100,
        "participants": {
            domain: {
                "plan": {"mission": {"id": domain, "objective": "unit original goal"}},
                "goal_verifier": {},
            }
            for domain in ("unit-a", "unit-b")
        },
    }
    budget = {
        "issued": {
            "boot_id": "0" * 36,
            "namespace": [1, 2],
            "elapsed_ms": 1000,
            "resolution_ms": 10,
        },
        "duration_ms": 5000,
    }
    wire = {
        "schema_version": manifest["schema_version"],
        "manifest": manifest,
        "nonce": "a" * 64,
        "epoch": None,
        "decision": None,
        "commits": None,
    }
    intent = {
        "request_digest": _digest(wire),
        "sent_at_ms": 1000,
        "budget": budget | {"duration_ms": 1000},
        "profile_revision": 1,
        "reply": None,
        "received_at_ms": None,
        "received_kernel": None,
        "error": "original reply unknown",
        "observations": [],
    }
    record = {
        "manifest": manifest,
        "root_epoch": "unit-original-writer",
        "principal": "unit",
        "leaf": "sha256:" + "1" * 64,
        "certificate_expires_at_ms": 20000,
        "budget": budget,
        "nonces": {"unit-a": "a" * 64, "unit-b": "b" * 64},
        "probes": {},
        "clocks": {},
        "prepared": {},
        "decision": None,
        "commits": {},
        "released": [],
        "intents": {"unit-a": {"probe": intent}},
        "completed": {},
        "stopping": None,
        "cleanup_budget": None,
        "stopped": [],
    }
    peer = {
        "address": "127.0.0.1:1",
        "server_name": "localhost",
        "server_leaf": "sha256:" + "2" * 64,
        "ca_file": "/unit/ca",
        "ca_digest": "sha256:" + "3" * 64,
        "client_cert": "/unit/cert",
        "client_leaf": "sha256:" + "4" * 64,
        "client_key": "/unit/key",
        "principal_id": "unit",
    }
    profile = {
        "schema_version": "roboguide.federation-peers/v0.1",
        "profile_id": "unit",
        "revision": 1,
        "local_domain": "unit-a",
        "max_drift_ppm": 100,
        "request_budget_ms": 1000,
        "peers": {"unit-a": peer, "unit-b": peer},
    }
    return {"profiles": {"1": profile}, "transactions": {"unit-original": record}}


def test_original_uncertain_request_is_not_completed_or_rewritten() -> None:
    """Readonly validation retains the original absent response and kernel budget exactly."""
    value = original_intent()
    before = copy.deepcopy(value)
    _coordinator(value)
    assert value == before


@pytest.mark.parametrize("budget", [100, 10001, 30000])
def test_explicit_budget_upgrade_keeps_original_unknown_and_clock(budget: int) -> None:
    """A new profile is readonly history, never a renewal of an old uncertain request."""
    value = original_intent()
    modern = copy.deepcopy(value["profiles"]["1"])
    modern.update(
        schema_version="roboguide.federation-peers/v0.2",
        budget_scope="intent-publication-and-peer-io",
        revision=2,
        request_budget_ms=budget,
    )
    value["profiles"]["2"] = modern
    before = copy.deepcopy(value)
    _coordinator(value)
    assert value == before


@pytest.mark.parametrize(
    "damage", ["legacy-range", "missing-scope", "wrong-scope", "new-range", "downgrade"]
)
def test_request_budget_contract_refuses_shape_changes_and_downgrade(damage: str) -> None:
    """Do not interpret new request limits under old authority or erase original generations."""
    value = original_intent()
    modern = copy.deepcopy(value["profiles"]["1"])
    modern.update(
        schema_version="roboguide.federation-peers/v0.2",
        budget_scope="intent-publication-and-peer-io",
        revision=2,
        request_budget_ms=30000,
    )
    value["profiles"]["2"] = modern
    if damage == "legacy-range":
        value["profiles"]["1"]["request_budget_ms"] = 10001
    elif damage == "missing-scope":
        del modern["budget_scope"]
    elif damage == "wrong-scope":
        modern["budget_scope"] = "peer-io-only"
    elif damage == "new-range":
        modern["request_budget_ms"] = 30001
    else:
        value["profiles"]["3"] = copy.deepcopy(value["profiles"]["1"])
        value["profiles"]["3"]["revision"] = 3
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        _coordinator(value)
    assert value == before


@pytest.mark.parametrize("damage", ["wire", "nonce", "profile", "decision", "release", "budget"])
def test_original_global_identity_cannot_be_replaced(damage: str) -> None:
    """Refuse changed effect bytes, borrowed peer authority and invented phase completion."""
    value = original_intent()
    record = value["transactions"]["unit-original"]
    intent = record["intents"]["unit-a"]["probe"]
    if damage == "wire":
        intent["request_digest"] = "sha256:" + "f" * 64
    elif damage == "nonce":
        record["nonces"]["unit-a"] = "c" * 64
    elif damage == "profile":
        value["profiles"] = {}
    elif damage == "decision":
        record["decision"] = {
            "manifest_digest": "sha256:" + "a" * 64,
            "prepared": {},
            "digest": "sha256:" + "b" * 64,
        }
    elif damage == "release":
        record["released"] = ["unit-a"]
    else:
        record["budget"]["duration_ms"] = 6000
    with pytest.raises(ValueError):
        _coordinator(value)


def test_portable_rust_python_identity_retains_float_types() -> None:
    """Shared exact fixtures prevent exponent differences and literal-tag collisions on recovery."""
    path = (
        Path(__file__).resolve().parents[3]
        / "contracts/control/federation-execution-v0.1/identity-fixtures.json"
    )
    for fixture in json.loads(path.read_text()):
        assert _digest(fixture["value"]) == fixture["digest"]
    assert _digest(1.0) != _digest({"$f64_bits": "3ff0000000000000"})
    assert _digest(1.0) != _digest(1)


def test_profile_history_uses_numeric_revision_not_json_key_order() -> None:
    """JSON key ordering cannot invalidate a valid original peer history."""
    value = original_intent()
    original = value["profiles"]["1"]
    value["profiles"] = {
        str(index): copy.deepcopy(original) | {"revision": index} for index in range(1, 13)
    }
    _coordinator(json.loads(json.dumps(value, sort_keys=True)))
