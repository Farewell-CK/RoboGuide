"""Read-tool isolation, original identity provenance and visible reference publication."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject, JSONValue
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.agent_tools import ReadToolAdapter
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.workspace import WorkbenchSession
from test_workbench import inventory, no_model, node
from test_workbench_v2 import ObservedController


@contextmanager
def _workbench(
    tmp_path: Path, controller: ObservedController | None = None
) -> Iterator[tuple[WorkbenchSession, ObservedController]]:
    """Provide a production session store and explicitly synthetic read-only projections."""
    reader = controller or ObservedController()
    store = SessionStore(tmp_path / "sessions", "tools", {}, False)
    session = WorkbenchSession(reader, no_model, store, MissionActions(reader, store))
    try:
        yield session, reader
    finally:
        store.close()


def _evidence(result: ReadResult) -> list[JSONObject]:
    """Retain the host result metadata exactly as an agent-loop evidence item would."""
    return [
        {
            "text": result.text,
            "complete": result.complete,
            "components": cast(JSONValue, list(result.components)),
        }
    ]


def test_shadow_reads_do_not_change_original_visible_or_durable_state(tmp_path: Path) -> None:
    """Fresh tool results stay private until their exact host-owned references are published."""
    with _workbench(tmp_path) as (session, reader):
        session.displayed_nodes = ["old-node"]
        session.last_node = "old-node"
        session.last_raw = {"old": True}
        session.last_read_at = "old-clock"
        session.checkpoint()
        saved = session.store.path.read_bytes()
        adapter = ReadToolAdapter(session, "介绍节点", session.query_references())
        references = adapter.references
        result = adapter.read("read_cluster", {}, 3)
        assert reader.reads == ["inventory"]
        assert result.complete is True
        assert adapter.references is references
        assert references["displayed_nodes"] == ["a", "b"]
        assert session.displayed_nodes == ["old-node"] and session.last_node == "old-node"
        assert session.last_raw == {"old": True} and session.last_read_at == "old-clock"
        assert session.store.path.read_bytes() == saved
        assert session.store.data["commands"] == []
        assert "#1 a" in adapter.reference_summary(_evidence(result))
        assert "#2 b" in adapter.reference_summary(_evidence(result))
        adapter.publish(_evidence(result))
        assert session.displayed_nodes == ["a", "b"]
        assert session.last_read_at != "old-clock"
        assert session.store.path.read_bytes() == saved  # DisplayTurn owns the durable ACK.


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("read_cluster", {}),
        ("read_node", {"node_id": "a"}),
        ("search_capabilities", {"keyword": "compute"}),
        ("compare_nodes", {"node_ids": ["a", "b"]}),
        ("read_active_missions", {}),
        ("list_missions", {}),
        ("read_mission", {"mission_id": "m1"}),
        ("read_explanation", {"mission_id": "m1"}),
    ],
)
def test_frozen_input_cannot_replace_an_unread_visible_list(
    tmp_path: Path, name: str, arguments: JSONObject
) -> None:
    """Publishing one object domain preserves newer visible lists in every unread domain."""
    with _workbench(tmp_path) as (session, _reader):
        session.displayed_nodes = ["old-node"]
        session.displayed_missions = ["old-mission"]
        original = session.query_references()
        session.displayed_nodes = ["new-node"]
        session.displayed_missions = ["new-mission"]
        adapter = ReadToolAdapter(session, "查询 a b m1", original)
        result = adapter.read(name, arguments, 3)
        adapter.publish(_evidence(result))
        if name in {"read_cluster", "read_node", "search_capabilities", "compare_nodes"}:
            assert session.displayed_missions == ["new-mission"]
        else:
            assert session.displayed_nodes == ["new-node"]
            if name in {"read_mission", "read_explanation"}:
                assert session.displayed_missions == ["new-mission"]
        assert session.store.data["commands"] == []


def test_tool_result_ids_remain_available_after_a_later_read_refocuses_the_list(
    tmp_path: Path,
) -> None:
    """An earlier result keeps its original cohort when another read narrows the list."""
    with _workbench(tmp_path) as (session, reader):
        adapter = ReadToolAdapter(session, "比较这两个节点", session.query_references())
        adapter.read("read_cluster", {}, 3)
        adapter.read("read_node", {"node_id": "a"}, 3)
        second = adapter.read("read_node", {"node_id": "b"}, 3)
        assert "1. b" in second.text and "1. a" not in second.text
        assert reader.reads == ["inventory"] * 3
        assert adapter.references["last_node"] == "b" and session.last_node is None


@pytest.mark.parametrize(
    "name,args",
    [
        ("read_node", {"node_id": "invented"}),
        ("read_mission", {"mission_id": "invented"}),
        ("read_explanation", {"mission_id": "invented"}),
        ("compare_nodes", {"node_ids": ["invented"]}),
    ],
)
def test_invented_target_is_rejected_before_any_get(
    tmp_path: Path, name: str, args: JSONObject
) -> None:
    """Typed object domains never borrow guessed IDs from model arguments."""
    with _workbench(tmp_path) as (session, reader):
        adapter = ReadToolAdapter(session, "看看状态", session.query_references())
        with pytest.raises(WorkbenchError, match="依据"):
            adapter.read(name, args, 3)
        assert reader.reads == [] and adapter.reference_summary() == ""


@pytest.mark.parametrize(
    "name,args",
    [
        ("propose_action", {"action": "execute"}),
        ("shell", {"command": "anything"}),
        ("read_cluster", {"url": "http://other-host"}),
        ("read_mission", {"mission_id": "m1", "method": "POST"}),
        ("read_cluster", {"index": 1}),
        ("search_capabilities", {"keyword": ""}),
        ("compare_nodes", {"node_ids": []}),
    ],
)
def test_only_closed_reads_can_enter_the_adapter(
    tmp_path: Path, name: str, args: JSONObject
) -> None:
    """Proposals, HTTP methods and unknown parameters never acquire an execution path."""
    with _workbench(tmp_path) as (session, reader):
        adapter = ReadToolAdapter(session, "m1", session.query_references())
        with pytest.raises(WorkbenchError):
            adapter.read(name, args, 3)
        assert reader.reads == [] and session.store.data["commands"] == []


def test_explicit_literal_mission_target_is_not_reinterpreted_as_a_command(tmp_path: Path) -> None:
    """An exact user-provided Mission ID reaches only the reader's literal-identity method."""
    with _workbench(tmp_path) as (session, reader):
        target = "mission#literal;statement"
        adapter = ReadToolAdapter(session, "查看 " + target, session.query_references())
        result = adapter.read("read_mission", {"mission_id": target}, 3)
        assert reader.reads == ["mission:" + target]
        assert target in adapter.reference_summary(_evidence(result))
        adapter.publish(_evidence(result))
        assert session.last_mission == target and session.store.data["commands"] == []


class IncompleteState(ObservedController):
    """Return one canonical accepted target whose current runtime lifecycle is unavailable."""

    def missions(self) -> JSONObject:
        """Retain a real fixture object ID without inventing Running or a successful empty set."""
        self.reads.append("missions")
        return {
            "schema": "roboguide.state-query/v0.1",
            "records": [
                {
                    "object": {
                        "class": "roboguide",
                        "object_type": "mission",
                        "object_id": "unknown",
                    },
                    "semantic": "desired",
                    "source": "roboguide:mission-orchestrator",
                    "channel_id": "accepted-plan",
                    "value": {"objective": "巡检"},
                }
            ],
        }


def test_partial_current_state_remains_unknown_and_requires_explicit_publication(
    tmp_path: Path,
) -> None:
    """An incomplete current cohort keeps both source completeness and its unavailable status."""
    with _workbench(tmp_path, IncompleteState()) as (session, reader):
        adapter = ReadToolAdapter(session, "当前任务", session.query_references())
        result = adapter.read("read_active_missions", {}, 3)
        assert result.complete is False and "Unknown" in result.text
        assert session.displayed_missions == []
        assert adapter.references["displayed_missions"] == ["unknown"]
        adapter.publish(_evidence(result))
        assert session.displayed_missions == ["unknown"]
        assert reader.reads == ["missions"] and session.store.data["commands"] == []


def test_only_retained_host_evidence_can_publish_a_successful_read(tmp_path: Path) -> None:
    """A later private read absent from final evidence cannot replace the displayed references."""
    with _workbench(tmp_path) as (session, _reader):
        adapter = ReadToolAdapter(session, "介绍节点", session.query_references())
        original = adapter.read("read_cluster", {}, 3)
        adapter.read("read_node", {"node_id": "b"}, 3)
        assert "#2 b" in adapter.reference_summary(_evidence(original))
        adapter.publish([])
        assert session.displayed_nodes == []
        adapter.publish(_evidence(original))
        assert session.displayed_nodes == ["a", "b"] and session.last_node is None


def test_evidence_from_another_adapter_has_no_reference_publication_authority(
    tmp_path: Path,
) -> None:
    """An earlier turn's valid source observation does not authorize this turn's private state."""
    with _workbench(tmp_path) as (session, _reader):
        first = ReadToolAdapter(session, "节点", session.query_references())
        first_result = first.read("read_cluster", {}, 3)
        second = ReadToolAdapter(session, "节点 a", session.query_references())
        second.read("read_node", {"node_id": "a"}, 3)
        second.publish(_evidence(first_result))
        assert session.displayed_nodes == []
        assert second.reference_summary(_evidence(first_result)) == ""


class TimeoutWitness(ObservedController):
    """Record the exact Controller timeout used by one shadow diagnostic read."""

    def __init__(self) -> None:
        """Keep a shared observation list so copying the read client retains test evidence."""
        super().__init__()
        self.timeouts: list[float] = []

    def inventory(self) -> JSONObject:
        """Observe the actual effective timeout before returning the normal typed fixture."""
        self.timeouts.append(self.timeout)
        return super().inventory()


def test_shadow_timeout_is_bounded_and_does_not_change_watch_or_foreground_client(
    tmp_path: Path,
) -> None:
    """The original read client keeps its timeout while the shadow consumes remaining turn time."""
    witness = TimeoutWitness()
    with _workbench(tmp_path, witness) as (session, _reader):
        adapter = ReadToolAdapter(session, "节点", session.query_references())
        adapter.read("read_cluster", {}, 0.25)
        assert witness.timeout == 5 and witness.timeouts == [0.25]
        with pytest.raises(WorkbenchError, match="预算"):
            adapter.read("read_cluster", {}, 0)
        assert witness.timeouts == [0.25]


class LargeInventory(ObservedController):
    """Return many original nodes whose long contracts require a bounded display projection."""

    def inventory(self) -> JSONObject:
        """Build a current complete source with enough unique contracts to exceed model context."""
        self.reads.append("inventory")
        nodes = []
        for index in range(128):
            current = node(f"node-{index:03}")
            current["contracts"] = [f"contract-{index}-" + "描述" * 150]
            nodes.append(current)
        return inventory(*nodes)


def test_large_source_keeps_complete_counts_but_only_rendered_ids_can_be_reused(
    tmp_path: Path,
) -> None:
    """A display budget never lends hidden IDs to later tool calls or visible ordinals."""
    with _workbench(tmp_path, LargeInventory()) as (session, reader):
        adapter = ReadToolAdapter(session, "节点", session.query_references())
        result = adapter.read("read_cluster", {}, 3)
        assert result.complete is True and len(result.text.encode()) <= 22_000
        assert "128 个" in result.text
        shown = cast(list[str], adapter.references["displayed_nodes"])
        assert 0 < len(shown) < 128
        with pytest.raises(WorkbenchError, match="依据"):
            adapter.read("read_node", {"node_id": "node-127"}, 3)
        adapter.publish(_evidence(result))
        assert session.displayed_nodes == shown
        assert reader.reads == ["inventory"]


def test_strict_tool_definitions_cannot_widen_host_dispatch_when_mutated(tmp_path: Path) -> None:
    """Definitions are independent copies and every native function has a closed strict schema."""
    with _workbench(tmp_path) as (session, _reader):
        adapter = ReadToolAdapter(session, "节点", session.query_references())
        definitions = adapter.definitions()
        for definition in definitions:
            assert definition["type"] == "function" and definition["strict"] is True
            schema = cast(JSONObject, definition["parameters"])
            assert schema["additionalProperties"] is False
            assert set(cast(list[str], schema["required"])) == set(
                cast(JSONObject, schema["properties"])
            )
        changed = copy.deepcopy(definitions)
        changed[0]["name"] = "shell"
        assert adapter.definitions() == definitions
