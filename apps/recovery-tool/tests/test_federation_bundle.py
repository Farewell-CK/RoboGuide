"""Synthetic reference-graph unit counterexamples; no domain, process or device facts."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from roboguide_recovery.bundle import Budget
from roboguide_recovery.federation_bundle import validate_domains


def fixture() -> tuple[list[tuple[str, list[dict[str, Any]], dict[str, Any]]], Budget]:
    """Build labelled minimal upstream-validated graph shapes, not deployable authority receipts."""
    manifest = {"coordinator_domain": "root", "participants": {"root": {}, "peer": {}}}
    receipt = {"units": 1}
    root_record = {
        "manifest": manifest,
        "nonces": {"root": "a", "peer": "b"},
        "prepared": {"peer": receipt},
        "commits": {},
        "decision": None,
        "released": [],
        "completed": {},
        "stopped": [],
    }
    root = {
        "policies": {"1": {"local_domain": "root"}},
        "executions": {"probes": {}, "prepared": {}},
        "coordinations": {"profiles": {}, "transactions": {"original": root_record}},
    }
    peer = {
        "policies": {"1": {"local_domain": "peer"}},
        "coordinations": {"profiles": {}, "transactions": {}},
        "executions": {
            "probes": {"original": {"nonce": "b", "manifest": manifest}},
            "prepared": {
                "original": {
                    "receipt": receipt,
                    "stopped": None,
                    "goal_evidence": {},
                    "committed": None,
                    "decision": None,
                    "release": None,
                }
            },
        },
    }
    states: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    for profile, federation in [("original-root", root), ("original-peer", peer)]:
        states.append(
            (
                profile,
                [],
                {
                    "candidate": {
                        "batch": {
                            "checkpoint": {
                                "checkpoint_json": json.dumps({"federation": federation})
                            }
                        }
                    }
                },
            )
        )
    return states, Budget({"max_bytes": 1048576, "max_files": 100, "budget_seconds": 30})


def update(
    states: Sequence[tuple[str, list[dict[str, Any]], dict[str, Any]]],
    index: int,
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    """Mutate only the explicit unit projection, retaining the other original-domain inputs."""
    cell = states[index][2]["candidate"]["batch"]["checkpoint"]
    wrapper = json.loads(cell["checkpoint_json"])
    mutate(wrapper["federation"])
    cell["checkpoint_json"] = json.dumps(wrapper)


def test_missing_active_peer_authority_refuses() -> None:
    """A root receipt cannot replace the peer's complete original backing."""
    states, budget = fixture()
    validate_domains(states, budget)
    with pytest.raises(ValueError, match="complete original domains"):
        validate_domains(states[:1], budget)


def test_missing_original_prepare_refuses() -> None:
    """A present domain label cannot stand in for the actual original prepared reference."""
    states, budget = fixture()
    update(states, 1, lambda value: value["executions"]["prepared"].clear())
    with pytest.raises(ValueError, match="original peer store"):
        validate_domains(states, budget)


def test_type_alias_cannot_replace_original_receipt() -> None:
    """Python bool/int equality cannot lend another resource-proof identity."""
    states, budget = fixture()
    update(
        states,
        1,
        lambda value: value["executions"]["prepared"]["original"].update(receipt={"units": True}),
    )
    with pytest.raises(ValueError, match="original peer store"):
        validate_domains(states, budget)


def test_conflicting_domain_profiles_refuse() -> None:
    """Independent clusters cannot silently claim the same original local domain."""
    states, budget = fixture()
    states.append(("different-profile", [], copy.deepcopy(states[0][2])))
    with pytest.raises(ValueError, match="same original Control domain"):
        validate_domains(states, budget)


def test_terminal_history_needs_no_deleted_peer_authority() -> None:
    """Complete original stop coverage is history, independently of unfinished local reports."""
    states, budget = fixture()
    update(
        states,
        0,
        lambda value: value["coordinations"]["transactions"]["original"].update(
            stopped=["root", "peer"]
        ),
    )
    validate_domains(states[:1], budget)
