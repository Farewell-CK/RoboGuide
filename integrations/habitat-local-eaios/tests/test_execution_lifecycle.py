"""Cross-check lifecycle declarations and reuse isolation without a model or simulator."""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from habitat_local_eaios.execution_lifecycle import (  # noqa: E402
    EXECUTION_LIFECYCLE_SCHEMA,
    lifecycle_declaration,
    unwrap_execution_lifecycle,
)
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.relocation_preflight import _check_execution_profile  # noqa: E402
from roboguide_eval.b1_deployment_feasibility import (  # type: ignore[import-untyped]  # noqa: E402
    _unwrap_execution_lifecycle_profile,
    preflight_deployment_feasibility,
)
from test_live_relocation_sources import live_relocation_run  # noqa: E402
from test_live_world import Agent, bind_task, task_bindings, vendor_boundary  # noqa: E402, F401


@pytest.mark.parametrize("relocation,acquisition", [(True, False), (False, True), (True, True)])
def test_producer_and_independent_reader_require_exact_effective_policy(
    relocation: bool, acquisition: bool
) -> None:
    """Missing, altered, extra, nested and mistyped declarations cannot borrow a new scope."""
    prior = {"schema_version": "roboguide.habitat-local-how-profile/v0.19"}
    profile = {
        "schema_version": EXECUTION_LIFECYCLE_SCHEMA,
        "predecessor_profile": prior,
        **lifecycle_declaration(relocation=relocation, acquisition=acquisition),
    }
    declared = {
        "relocation_completion_binding": relocation,
        "enable_observation_acquisition": acquisition,
    }
    assert (
        unwrap_execution_lifecycle(
            profile, live=True, relocation=relocation, acquisition=acquisition
        )
        is prior
    )
    assert _unwrap_execution_lifecycle_profile(profile, declared) is prior
    for mutation in ["missing", "extra", "budget", "type", "nested", "scope"]:
        broken = deepcopy(profile)
        if mutation == "missing":
            del broken["peer_message_lifecycle"]
        elif mutation == "extra":
            broken["invented_authority"] = True
        elif mutation == "budget":
            broken["stage2_working_context"]["max_serialized_message_bytes"] *= 2
        elif mutation == "type":
            broken["stage2_working_context"]["model_response_rewritten"] = 0
        elif mutation == "nested":
            broken["predecessor_profile"] = profile
        else:
            broken["stage2_working_context"]["scope"] = "native-emos"
        with pytest.raises(IntegrationError):
            unwrap_execution_lifecycle(
                broken, live=True, relocation=relocation, acquisition=acquisition
            )
        with pytest.raises(ValueError):
            _unwrap_execution_lifecycle_profile(broken, declared)


@pytest.mark.parametrize("recoveries", [0, 2])
@pytest.mark.parametrize("bad_base", [False, True])
def test_complete_preflight_keeps_original_source_and_authority_checks(
    tmp_path: Path, recoveries: int, bad_base: bool
) -> None:
    """A valid outer declaration cannot hide a changed original predecessor or frozen source."""
    run = live_relocation_run(tmp_path, version=6, recoveries=recoveries)
    path = run / "evidence/local-how-profile.json"
    prior = json.loads(path.read_bytes())
    if bad_base:
        prior["official_success_authority"] = "invented-local-success"
    profile = {
        "schema_version": EXECUTION_LIFECYCLE_SCHEMA,
        "predecessor_profile": prior,
        **lifecycle_declaration(relocation=True, acquisition=False),
    }
    path.write_text(json.dumps(profile))
    frozen = hashlib.sha256((run / "b1-input-used.json").read_bytes()).hexdigest()
    if bad_base:
        with pytest.raises(ValueError):
            preflight_deployment_feasibility(run)
        with pytest.raises(IntegrationError):
            _check_execution_profile(run, frozen)
    else:
        preflight_deployment_feasibility(run)
        assert (
            _check_execution_profile(run, frozen)["local_how_schema"] == EXECUTION_LIFECYCLE_SCHEMA
        )


def test_retired_attempt_messages_cannot_reach_next_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endpoint reuse discards old unread advisory messages and preserves the live peer inbox."""
    queues = {"agent_0": [], "agent_1": ["still for the active peer"]}
    monkeypatch.setattr(Agent, "message_pipe", queues, raising=False)
    bindings, runtime, _ = task_bindings(tmp_path)
    old = bind_task(bindings, 0, 0)
    runtime._bounded_execution_lifecycle = True
    queues["agent_0"].extend(["old task request", "another old request"])
    bindings.finish(0)
    assert "agent_0" not in queues and queues["agent_1"] == ["still for the active peer"]
    new = bind_task(bindings, 0, 1)
    assert old.request_key() != new.request_key() and "agent_0" not in queues
    bindings.close()
    rows = [
        json.loads(line)
        for line in (tmp_path / "stage2-execution-feedback.jsonl").read_text().splitlines()
    ]
    retired = [row for row in rows if row.get("event") == "attempt-inbox-retired"]
    assert len(retired) == 1 and retired[0]["unread_message_count"] == 2
    assert retired[0]["delivery_acknowledged"] is False


def test_legacy_inbox_policy_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An older runtime does not silently acquire the new attempt lifecycle."""
    queues: dict[str, list[str]] = {"agent_0": []}
    monkeypatch.setattr(Agent, "message_pipe", queues, raising=False)
    bindings, _, _ = task_bindings(tmp_path)
    bind_task(bindings, 0, 0)
    queues["agent_0"].append("legacy pending message")
    bindings.finish(0)
    assert queues["agent_0"] == ["legacy pending message"]
    bindings.close()


def test_inbox_archival_failure_cannot_reenable_stale_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable evidence sink never turns old unread messages into successor input."""
    queues: dict[str, list[str]] = {"agent_0": [], "agent_1": ["active peer message"]}
    monkeypatch.setattr(Agent, "message_pipe", queues, raising=False)
    bindings, runtime, _ = task_bindings(tmp_path)
    bind_task(bindings, 0, 0)
    runtime._bounded_execution_lifecycle = True
    queues["agent_0"].append("old task message")

    def unavailable(_: object) -> None:
        """Keep a failed evidence volume separate from inbox lifecycle."""
        raise OSError("evidence unavailable")

    monkeypatch.setattr(bindings, "record_feedback", unavailable)
    bindings.finish(0)
    assert "agent_0" not in queues and queues["agent_1"] == ["active peer message"]
    bindings.close()
