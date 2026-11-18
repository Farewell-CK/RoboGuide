"""Host-owned read tools with isolated observations and explicit reference publication."""

from __future__ import annotations

import copy
import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from mission.models import JSONObject, JSONValue

from roboguide_workbench.client import ToolRefusal, WorkbenchError, identifier
from roboguide_workbench.device_context import alias_matches, aliases
from roboguide_workbench.evidence import READ_COSTS, ReadResult
from roboguide_workbench.routing import Query
from roboguide_workbench.session import Session
from roboguide_workbench.state_queries import (
    StateSnapshot,
    choices,
    discover,
    read_state,
)
from roboguide_workbench.state_queries import (
    fields as state_fields,
)
from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession

_PROJECTION_BYTES = 22_000
_READ_SOURCE = "read-tool-adapter"
_READ_SCOPE = "configured-controller/current-identity"
_ACTIONS = ["plan", "run", "answer", "execute", "approve"]


def _function(name: str, description: str, properties: JSONObject) -> JSONObject:
    """Build one strict native Responses function definition with closed required keys."""
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties),
        },
    }


def _text_parameter(description: str) -> JSONObject:
    """Describe one bounded literal identifier or search phrase without executable syntax."""
    return {"type": "string", "minLength": 1, "maxLength": 256, "description": description}


_DEFINITIONS = [
    _function("read_cluster", "读取当前身份可见的节点数量、能力和合同。", {}),
    _function("read_active_missions", "读取当前活动及状态未知的 Mission，终态分别统计。", {}),
    _function("list_missions", "读取当前身份可见的全部 Mission 状态。", {}),
    _function(
        "list_state_fields",
        "查看设备已声明的状态字段；target 留空列出可见对象。",
        {"target": {"type": "string", "maxLength": 256}},
    ),
    _function(
        "read_state",
        "读取设备已上报状态，消耗三次 GET；字段可用中文或目录中的名称。",
        {
            "target": _text_parameter("用户提供或目录返回的原始设备/对象 ID。"),
            "fields": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
            },
        },
    ),
    _function(
        "read_node",
        "按用户提供或本轮工具结果中的原始 ID 读取节点。",
        {
            "node_id": _text_parameter("原始节点 ID，不是显示序号。"),
        },
    ),
    _function(
        "read_mission",
        "按用户提供或本轮工具结果中的原始 ID 读取 Mission。",
        {
            "mission_id": _text_parameter("原始 Mission ID，不是显示序号。"),
        },
    ),
    _function(
        "search_capabilities",
        "按具体能力或合同关键词检索当前节点。",
        {
            "keyword": _text_parameter("具体能力或合同关键词，例如建图或 compute。"),
        },
    ),
    _function(
        "read_explanation",
        "读取 Mission、绑定和恢复依据，消耗三次只读请求。",
        {
            "mission_id": _text_parameter("原始 Mission ID，不是显示序号。"),
        },
    ),
    _function(
        "compare_nodes",
        "读取并比较一至四个已经提供的原始节点 ID。",
        {
            "node_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": _text_parameter("原始节点 ID，不是显示序号。"),
            },
        },
    ),
    _function(
        "propose_action",
        "旧客户端兼容提议；默认任务必须使用 start_task，勿使用此兼容入口。",
        {
            "action": {"type": "string", "enum": cast(JSONValue, _ACTIONS)},
            "start_turn": {
                "type": "string",
                "minLength": 1,
                "maxLength": 16,
                "description": "proposal_sources 中任务最早的用户原文 ID；独立新任务用 current。",
            },
        },
    ),
]
_FIELDS = {
    cast(str, tool["name"]): set(
        cast(JSONObject, cast(JSONObject, tool["parameters"])["properties"])
    )
    for tool in _DEFINITIONS
}


def _ids(value: JSONValue) -> list[str]:
    """Validate bounded host reference identities before allowing a model to reuse them."""
    if not isinstance(value, list) or len(value) > 128:
        raise WorkbenchError("对象引用列表无效。")
    if any(not isinstance(item, str) for item in value):
        raise WorkbenchError("对象引用 ID 无效。")
    return [identifier(cast(str, item)) for item in value]


def _optional_id(value: JSONValue) -> str | None:
    """Keep an absent prior identity unavailable instead of filling it from another domain."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkbenchError("对象引用 ID 无效。")
    return identifier(value)


def _bounded_projection(text: str) -> tuple[str, bool]:
    """Bound model/display projection at complete lines without changing source completeness."""
    if len(text.encode("utf-8")) <= _PROJECTION_BYTES:
        return text, False
    marker = "\n…显示已截断；未展示部分不作为对象引用，统计数量仍来自完整读取。"
    remaining = _PROJECTION_BYTES - len(marker.encode("utf-8"))
    selected: list[str] = []
    for line in text.splitlines(keepends=True):
        size = len(line.encode("utf-8"))
        if size > remaining:
            break
        selected.append(line)
        remaining -= size
    return "".join(selected).rstrip() + marker, True


def _rendered_ids(ids: list[str], text: str) -> list[str]:
    """Keep only complete numbered identity rows actually included in this host projection."""
    lines = set(text.splitlines())
    result = []
    for index, target in enumerate(ids, 1):
        prefix = f"{index}. {safe_text(target)}"
        if prefix in lines or any(line.startswith(prefix + "：") for line in lines):
            result.append(target)
    return result


@dataclass(frozen=True)
class _Projection:
    """Freeze one successful host read independently of later tool work or model wording."""

    read_id: str
    node_ids: tuple[str, ...]
    mission_ids: tuple[str, ...]
    last_node: str | None
    last_mission: str | None
    raw: JSONObject | None
    received_at: str | None
    nodes_read: bool
    missions_read: bool
    single_mission: str | None
    states: tuple[str, ...]
    last_state: str | None
    states_read: bool
    state_focus: bool


class ReadToolAdapter:
    """Execute only closed reads on a private shadow and publish separately after display."""

    def __init__(self, session: WorkbenchSession, question: str, references: JSONObject) -> None:
        """Freeze input scope while keeping all writes, checkpoints and visible selections owned."""
        self.session = session
        self.question = question
        self.references = copy.deepcopy(references)
        self._shadow = copy.copy(session)
        controller = copy.copy(session.controller)
        self._shadow.controller = controller
        self._shadow.reader = controller
        self._shadow.displayed_nodes = _ids(references.get("displayed_nodes", []))
        self._shadow.displayed_missions = _ids(references.get("displayed_missions", []))
        self._shadow.last_node = _optional_id(references.get("last_node"))
        self._shadow.last_mission = _optional_id(references.get("last_mission"))
        self._shadow.displayed_states = _ids(references.get("state_targets", []))
        self._shadow.last_state = _optional_id(references.get("last_state"))
        self._shadow.last_raw = copy.deepcopy(session.last_raw)
        self._shadow.input_references = None
        self._shadow.input_history = None
        self._shadow.shown_draft = None
        self._shadow.suggestion = None
        self._shadow.pending_cancel = None
        self._nonce = uuid.uuid4().hex
        self._projections: list[_Projection] = []
        self._nodes_read = False
        self._missions_read = False
        self._single_mission: str | None = None
        self._states_read = False
        self._state_focus = False
        self._state_reads: dict[str, StateSnapshot] = {}
        self._read_results: dict[str, ReadResult] = {}
        self._known_nodes = set(self._shadow.displayed_nodes)
        self._known_missions = set(self._shadow.displayed_missions)
        if self._shadow.last_node:
            self._known_nodes.add(self._shadow.last_node)
        if self._shadow.last_mission:
            self._known_missions.add(self._shadow.last_mission)
        self._known_states = set(self._shadow.displayed_states) | self._known_nodes
        if self._shadow.last_state:
            self._known_states.add(self._shadow.last_state)
        self._update_references()

    def definitions(self) -> list[JSONObject]:
        """Return fresh native schemas; model-visible schema edits cannot widen host dispatch."""
        return copy.deepcopy(_DEFINITIONS)

    def learn_task(self, nodes: set[str]) -> None:
        """Admit only actual current Mission receipt IDs created by the same foreground owner."""
        self._known_nodes.update(nodes)
        self._known_states.update(nodes)
        if self.session.last_mission:
            self._known_missions.add(self.session.last_mission)
            self._shadow.last_mission = self.session.last_mission
            self._update_references()

    def task_missions(self, result: ReadResult) -> set[str]:
        """Export exact successful Mission identities, excluding model text and failed cohorts."""
        if not result.complete:
            return set()
        targets: set[str] = set()
        for component in result.components:
            if (
                component.get("source") != _READ_SOURCE
                or component.get("scope") != _READ_SCOPE
                or component.get("tool")
                not in {"read_active_missions", "list_missions", "read_mission", "read_explanation"}
            ):
                continue
            for projection in self._projections:
                if projection.read_id == component.get("read_id"):
                    targets.update(projection.mission_ids)
                    if projection.single_mission:
                        targets.add(projection.single_mission)
        return targets

    def _update_references(self) -> None:
        """Update the shared model-context object in place after a validated original read."""
        self.references.update(
            {
                "displayed_nodes": list(self._shadow.displayed_nodes),
                "displayed_missions": list(self._shadow.displayed_missions),
                "last_node": self._shadow.last_node,
                "last_mission": self._shadow.last_mission,
                "state_targets": list(self._shadow.displayed_states),
                "last_state": self._shadow.last_state,
            }
        )

    def _target(self, arguments: JSONObject, field: str, domain: str) -> str:
        """Refuse an invented target before any GET, preserving frozen input/result origins."""
        value = arguments.get(field)
        if not isinstance(value, str):
            raise WorkbenchError("读取工具需要原始对象 ID。")
        target = identifier(value)
        known = (
            self._known_nodes
            if domain == "nodes"
            else self._known_states
            if domain == "states"
            else self._known_missions
        )
        if target not in known and target not in self.question:
            if domain == "states" and alias_matches(target, self.question):
                # The actual scoped catalog, not a model-generated suffix, selects candidates.
                return sorted(aliases(target) & aliases(self.question))[0]
            raise ToolRefusal(
                "对象 ID 没有当前问题、已展示引用或本轮读取依据；"
                "请先查目录，或使用用户原话中的设备名。"
            )
        return target

    def read(self, name: str, arguments: JSONObject, remaining: float) -> ReadResult:
        """Execute a validated read under the caller's original budget, without any mutation API."""
        fields = _FIELDS.get(name)
        if fields is None or name == "propose_action" or set(arguments) != fields:
            raise WorkbenchError("未开放这个读取工具或参数。")
        if isinstance(remaining, bool) or not math.isfinite(remaining) or remaining <= 0:
            raise WorkbenchError("本轮读取时间预算已耗尽。")
        target: str | None = None
        state_snapshot: StateSnapshot | None = None
        targets: tuple[str, ...] = ()
        if name == "read_node":
            target = self._target(arguments, "node_id", "nodes")
        elif name in {"read_mission", "read_explanation"}:
            target = self._target(arguments, "mission_id", "missions")
        elif name in {"list_state_fields", "read_state"}:
            if name == "list_state_fields" and arguments.get("target") == "":
                target = ""
            else:
                target = self._target(arguments, "target", "states")
        elif name == "search_capabilities":
            value = arguments.get("keyword")
            if not isinstance(value, str):
                raise WorkbenchError("能力检索需要具体关键词。")
            target = identifier(value)
        elif name == "compare_nodes":
            values = arguments.get("node_ids")
            if not isinstance(values, list) or not 1 <= len(values) <= 4:
                raise WorkbenchError("节点比较需要一至四个原始 ID。")
            targets = tuple(
                self._target({"node_id": value}, "node_id", "nodes") for value in values
            )
            if len(set(targets)) != len(targets):
                raise WorkbenchError("节点比较包含重复 ID。")
        cost = READ_COSTS.get(name, 1)
        controller_timeout = self._shadow.controller.timeout
        self._shadow.controller.timeout = min(controller_timeout, remaining / cost)
        try:
            if name == "list_state_fields":
                raw = self._shadow.controller.state_providers()
                result, targets_ = discover(raw, target or "")
                self._shadow.displayed_states = targets_
                self._shadow.last_state = targets_[0] if len(targets_) == 1 else None
                self._shadow.last_raw = raw
                self._states_read = True
                self._state_focus = True
            elif name == "read_state":
                values = arguments.get("fields")
                if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values
                ):
                    raise WorkbenchError("状态查询需要明确的字段列表。")
                # read_state itself clips every GET to the original remaining deadline.
                self._shadow.controller.timeout = min(controller_timeout, remaining)
                state_snapshot = read_state(
                    self._shadow.controller,
                    target or "",
                    state_fields(cast(list[str], values)),
                    min(60, remaining),
                )
                result = state_snapshot.render()
                selected_choices = choices(result)
                if selected_choices:
                    self._shadow.displayed_states = selected_choices
                    self._states_read = True
                self._shadow.last_state = target
                self._state_focus = True
                self._shadow.last_raw = {
                    "providers": state_snapshot.catalog,
                    "inventory": state_snapshot.inventory,
                    "state": state_snapshot.state,
                }
            elif name in {"read_active_missions", "list_missions"}:
                result = self._shadow.read_missions(active_only=name == "read_active_missions")
            elif name == "read_mission":
                result = ReadResult(self._shadow.read_mission(target or ""))
            elif name == "read_explanation":
                result = self._shadow.read_explanation(target or "")
            elif name == "compare_nodes":
                result = ReadResult(self._shadow.read_comparison(targets))
            else:
                kind = {
                    "read_cluster": "cluster",
                    "read_node": "node",
                    "search_capabilities": "capability",
                }[name]
                result = ReadResult(Session._execute(self._shadow, Query(kind, value=target)))
        finally:
            self._shadow.controller.timeout = controller_timeout
        text, truncated = _bounded_projection(result.text)
        if name in {"read_cluster", "read_node", "search_capabilities", "compare_nodes"}:
            self._nodes_read = True
            self._shadow.displayed_nodes = _rendered_ids(self._shadow.displayed_nodes, text)
            if self._shadow.last_node not in self._shadow.displayed_nodes:
                self._shadow.last_node = None
            self._single_mission = None
        elif name in {"read_active_missions", "list_missions"}:
            self._missions_read = True
            self._shadow.displayed_missions = _rendered_ids(self._shadow.displayed_missions, text)
            if self._shadow.last_mission not in self._shadow.displayed_missions:
                self._shadow.last_mission = None
            self._single_mission = None
        elif name in {"read_mission", "read_explanation"}:
            self._single_mission = target
        self._known_nodes.update(self._shadow.displayed_nodes)
        self._known_missions.update(self._shadow.displayed_missions)
        self._known_states.update(self._shadow.displayed_states)
        self._known_states.update(self._shadow.displayed_nodes)
        if self._shadow.last_state:
            self._known_states.add(self._shadow.last_state)
        if self._shadow.last_mission:
            self._known_missions.add(self._shadow.last_mission)
        self._update_references()
        read_id = hashlib.sha256(f"{self._nonce}:{len(self._projections)}".encode()).hexdigest()
        if state_snapshot is not None:
            self._state_reads[read_id] = state_snapshot
        if name in {"read_state", "list_state_fields"}:
            self._shadow.last_read_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._projections.append(
            _Projection(
                read_id,
                tuple(self._shadow.displayed_nodes),
                tuple(self._shadow.displayed_missions),
                self._shadow.last_node,
                self._shadow.last_mission,
                copy.deepcopy(self._shadow.last_raw),
                self._shadow.last_read_at,
                self._nodes_read,
                self._missions_read,
                self._single_mission,
                tuple(self._shadow.displayed_states),
                self._shadow.last_state,
                self._states_read,
                self._state_focus,
            )
        )
        component: JSONObject = {
            "source": _READ_SOURCE,
            "scope": _READ_SCOPE,
            "complete": result.complete,
            "read_id": read_id,
            "tool": name,
            "display_truncated": truncated,
            "node_ids": list(self._shadow.displayed_nodes),
            "mission_ids": list(self._shadow.displayed_missions),
        }
        returned = ReadResult(text, result.complete, (*result.components, component))
        self._read_results[read_id] = returned
        return returned

    def _selected(self, evidence: list[JSONObject] | None) -> _Projection | None:
        """Select only this adapter's reads retained in the actual final evidence bundle."""
        if evidence is None:
            return self._projections[-1] if self._projections else None
        read_ids: set[str] = set()
        for item in evidence:
            components = item.get("components")
            if not isinstance(components, list):
                continue
            for component in components:
                if isinstance(component, dict) and component.get("source") == _READ_SOURCE:
                    read_id = component.get("read_id")
                    if isinstance(read_id, str):
                        read_ids.add(read_id)
        return next(
            (
                projection
                for projection in reversed(self._projections)
                if projection.read_id in read_ids
            ),
            None,
        )

    def reference_summary(self, evidence: list[JSONObject] | None = None) -> str:
        """Render exact host references before they may become visible ordinal selections."""
        projection = self._selected(evidence)
        if projection is None:
            return ""
        lines: list[str] = []
        if projection.nodes_read:
            lines.append(
                "本轮节点引用："
                + (
                    "；".join(
                        f"#{i} {safe_text(value)}" for i, value in enumerate(projection.node_ids, 1)
                    )
                    or "无"
                )
            )
        if projection.missions_read:
            lines.append(
                "本轮任务引用："
                + (
                    "；".join(
                        f"#{i} {safe_text(value)}"
                        for i, value in enumerate(projection.mission_ids, 1)
                    )
                    or "无"
                )
            )
        if projection.single_mission:
            lines.append("本轮查询任务：" + safe_text(projection.single_mission))
        if projection.states_read:
            lines.append(
                "本轮状态对象："
                + (
                    "；".join(
                        f"#{i} {safe_text(value)}" for i, value in enumerate(projection.states, 1)
                    )
                    or "无"
                )
            )
        return "\n".join(lines)

    def read_answer(
        self, evidence: list[JSONObject], *, include_nonstate: bool = False
    ) -> ReadResult | None:
        """Recheck State and retain independent host reads beside native task receipts."""
        results: list[ReadResult] = []
        has_state = False
        for item in evidence:
            components = item.get("components")
            source, text = item.get("source"), item.get("text")
            if (
                not isinstance(components, list)
                and isinstance(source, str)
                and source in _FIELDS
                and source != "propose_action"
                and isinstance(text, str)
            ):
                results.append(ReadResult(text, item.get("complete") is True))
            if not isinstance(components, list):
                continue
            for component in components:
                if not isinstance(component, dict) or component.get("source") != _READ_SOURCE:
                    continue
                read_id = component.get("read_id")
                if not isinstance(read_id, str) or read_id not in self._read_results:
                    continue
                snapshot = self._state_reads.get(read_id)
                if snapshot is None:
                    results.append(self._read_results[read_id])
                    continue
                has_state = True
                result = snapshot.render()
                item["consumption_check"] = {
                    "complete": result.complete,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "fields": cast(JSONValue, list(result.components)),
                }
                results.append(result)
        if not has_state and not include_nonstate or not results:
            return None
        return ReadResult(
            "\n\n".join(result.text for result in results),
            all(result.complete for result in results),
            tuple(component for result in results for component in result.components),
        )

    def publish(self, evidence: list[JSONObject], *, publish_raw: bool = True) -> None:
        """Adopt only the exact host-rendered references; DisplayTurn still owns durable ACK."""
        projection = self._selected(evidence)
        if projection is None:
            return
        if projection.nodes_read:
            self.session.displayed_nodes = list(projection.node_ids)
        if projection.missions_read:
            self.session.displayed_missions = list(projection.mission_ids)
        if projection.nodes_read or projection.missions_read or projection.single_mission:
            self.session.last_node = projection.last_node
            self.session.last_mission = projection.last_mission
        if projection.states_read:
            self.session.displayed_states = list(projection.states)
        if projection.state_focus:
            self.session.last_state = projection.last_state
        if publish_raw:
            self.session.last_raw = copy.deepcopy(projection.raw)
            self.session.last_read_at = projection.received_at
