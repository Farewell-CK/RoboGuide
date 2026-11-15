"""Scoped State discovery, exact declaration joins and bounded host-rendered field queries."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from mission.models import JSONObject, JSONValue

from roboguide_workbench.client import ReadOnlyController, WorkbenchError, identifier
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.state_profiles import DEFAULT_PROFILES, StateProfile, StateValue
from roboguide_workbench.views import safe_text

LABELS = {"battery": "电量", "position": "位置", "posture": "姿态"}
ALIASES = {"电量": "battery", "位置": "position", "姿态": "posture"}


def fields(values: list[str]) -> tuple[str, ...]:
    """Validate a bounded field selection while preserving future profile-defined field names."""
    if not 1 <= len(values) <= 8:
        raise WorkbenchError("一次请选择 1–8 个状态字段，例如 battery、position、posture。")
    selected = tuple(ALIASES.get(identifier(value), value) for value in values)
    if len(set(selected)) != len(selected) or any(len(value) > 64 for value in selected):
        raise WorkbenchError("状态字段重复或过长。")
    return selected


@dataclass(frozen=True)
class Export:
    """Keep one current tenant-visible registration declaration with its original source key."""

    node: str
    object: JSONObject
    source: str
    channel: str
    semantic: str
    schema: str
    ttl: int
    profile: StateProfile | None

    def matches(self, row: JSONObject) -> bool:
        """Join original object, source, semantic and channel without borrowing another owner."""
        return all(
            (
                row.get("object") == self.object,
                row.get("source") == self.source,
                row.get("channel_id") == self.channel,
                row.get("semantic") == self.semantic,
            )
        )


def exports(raw: JSONObject, profiles: tuple[StateProfile, ...] = DEFAULT_PROFILES) -> list[Export]:
    """Validate complete declarations before using a single object identity or source binding."""
    if raw.get("schema") != "roboguide.state-provider-catalog/v0.1" or not isinstance(
        raw.get("nodes"), list
    ):
        raise WorkbenchError("状态上报目录不可用或格式不受支持。")
    nodes = cast(list[JSONValue], raw["nodes"])
    if len(nodes) > 4096:
        raise WorkbenchError("状态上报目录超出完整读取上限。")
    result: list[Export] = []
    seen_nodes: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
            raise WorkbenchError("状态上报目录的节点身份不完整。")
        node_id = identifier(cast(str, node["node_id"]))
        declarations = node.get("state_exports")
        if node_id in seen_nodes or not isinstance(declarations, list) or len(declarations) > 128:
            raise WorkbenchError("状态上报目录存在重复节点或不完整声明。")
        seen_nodes.add(node_id)
        keys: set[str] = set()
        for item in declarations:
            if not isinstance(item, dict):
                raise WorkbenchError("状态声明格式无效。")
            obj = item.get("object")
            if not isinstance(obj, dict) or set(obj) != {"class", "object_type", "object_id"}:
                raise WorkbenchError("状态对象声明无效。")
            if obj["class"] not in ("node", "world") or item.get("semantic") not in (
                "reported",
                "observed",
            ):
                raise WorkbenchError("状态声明超出只读设备范围。")
            names = [item.get(key) for key in ("local_system_id", "export_id", "payload_schema")]
            names += [obj.get("object_type"), obj.get("object_id")]
            if any(not isinstance(value, str) for value in names):
                raise WorkbenchError("状态声明缺少原始标识。")
            owner, channel, schema, kind, target = [identifier(cast(str, value)) for value in names]
            ttl = item.get("valid_for_ms")
            if type(ttl) is not int or ttl <= 0 or channel in keys:
                raise WorkbenchError("状态声明的有效期或来源重复。")
            keys.add(channel)
            profile = next(
                (
                    p
                    for p in profiles
                    if (p.schema, p.object_class, p.object_type) == (schema, obj["class"], kind)
                ),
                None,
            )
            result.append(
                Export(
                    node_id,
                    {"class": obj["class"], "object_type": kind, "object_id": target},
                    f"node:{node_id}/{owner}",
                    channel,
                    cast(str, item["semantic"]),
                    schema,
                    ttl,
                    profile,
                )
            )
    return result


def _selected(exports_: list[Export], target: str) -> list[Export]:
    """Keep every exact object/publisher match so ambiguous names never select a foreign object."""
    return [item for item in exports_ if item.object["object_id"] == target or item.node == target]


def discover(
    raw: JSONObject, target: str = "", profiles: tuple[StateProfile, ...] = DEFAULT_PROFILES
) -> tuple[ReadResult, list[str]]:
    """List declared fields; registration never proves a fresh measurement."""
    all_exports = exports(raw, profiles)
    selected = _selected(all_exports, target) if target else all_exports
    lines = [f"{safe_text(target)} 的状态字段" if target else "可查询状态的对象"]
    targets: list[str] = []
    for item in selected[:64]:
        object_id = cast(str, item.object["object_id"])
        index = targets.index(object_id) + 1 if object_id in targets else len(targets) + 1
        names = (
            "、".join(LABELS.get(name, name) + f" [{name}]" for name in item.profile.fields)
            if item.profile
            else "格式尚未接入解释器"
        )
        entry = [
            f"{index}. {safe_text(object_id)} · 上报节点 {safe_text(item.node)}",
            f"   {names}；来源 {safe_text(item.source)}；格式 {safe_text(item.schema)}",
        ]
        if len("\n".join([*lines, *entry]).encode()) > 18_000:
            lines.append("展示已截断；请指定明确对象 ID 缩小范围。")
            break
        lines.extend(entry)
        if object_id not in targets:
            targets.append(object_id)
    if not selected:
        lines.append("当前身份可见的目录中没有匹配的状态声明；请核对对象 ID 或设备的状态上报配置。")
    if len(selected) > 64:
        lines.append("只展示前 64 个声明；请指定明确的对象 ID 缩小范围。")
    lines.append("字段来自登记声明；实际数值仍需读取并检查有效期。")
    return ReadResult("\n".join(lines), bool(selected)), targets


@dataclass(frozen=True)
class StateSnapshot:
    """Retain original read bytes and local start so later rendering cannot renew observations."""

    target: str
    requested: tuple[str, ...]
    catalog: JSONObject
    inventory: JSONObject
    state: JSONObject
    started: float
    received_at: str
    profiles: tuple[StateProfile, ...] = DEFAULT_PROFILES

    def render(self) -> ReadResult:
        """Recheck original clocks and source-qualified fields at the actual consumption time."""
        requested = fields(list(self.requested))
        selected = _selected(exports(self.catalog, self.profiles), self.target)
        rows = self.state.get("records")
        observed = self.state.get("observed_at_ms")
        if (
            self.state.get("schema") != "roboguide.state-query/v0.1"
            or not isinstance(rows, list)
            or len(rows) > 16_384
            or type(observed) is not int
            or observed < 0
        ):
            raise WorkbenchError("状态响应缺少完整记录或 Controller 观察时间。")
        if self.inventory.get("schema_version") != "roboguide.inventory/v0.1" or not isinstance(
            self.inventory.get("nodes"), list
        ):
            raise WorkbenchError("无法核对状态上报节点的可达观测。")
        elapsed = (time.monotonic() - self.started) * 1000
        if elapsed < 0:
            raise WorkbenchError("本地读取时钟无效，当前状态未知。")
        results: list[JSONObject] = []
        lines = [
            f"{safe_text(self.target)} · 设备状态",
            f"读取时间：{self.received_at}（客户端 UTC）",
        ]
        for name in requested:
            candidates = [item for item in selected if item.profile and name in item.profile.fields]
            sample = StateValue("unsupported", reason="没有声明支持该字段的已接入状态格式")
            evidence: JSONObject = {"field": name, "source": None, "received_at_ms": None}
            if len(candidates) > 1:
                sample = StateValue(
                    "conflict",
                    reason="存在多个状态来源或对象，请先 /state-fields 查看并指定明确对象",
                )
                evidence["candidates"] = [cast(JSONValue, item.object) for item in candidates]
            elif candidates:
                item = candidates[0]
                matches = [row for row in rows if isinstance(row, dict) and item.matches(row)]
                sample, evidence = self._field(item, matches, name, observed, elapsed)
            elif not selected:
                sample = StateValue(
                    "unavailable", reason="未找到该对象的状态声明，或当前身份不可见"
                )
            row = {
                **evidence,
                "field": name,
                "status": sample.status,
                "value": sample.value,
                "unit": sample.unit,
                "reason": sample.reason,
                "details": sample.details,
                "remaining_ms": max(0, math.floor(sample.remaining_ms)),
                "complete": sample.status == "reported",
            }
            results.append(row)
            lines.append(_render_field(name, sample))
            if evidence.get("source"):
                lines.append(
                    f"  来源：{safe_text(cast(str, evidence['source']))}；"
                    f"Controller 接收时间：{evidence.get('received_at_ms')} ms"
                )
        lines.append("显示设备上报数据；位置、姿态不授予移动或物理安全许可。")
        complete = all(row["status"] == "reported" for row in results)
        if not complete:
            lines.append(
                "用 /state-fields 查看明确对象和可用字段；数据过期时请检查设备的状态上报。"
            )
        return ReadResult("\n".join(lines), complete, tuple(results))

    def _field(
        self, item: Export, matches: list[JSONObject], name: str, now: int, elapsed: float
    ) -> tuple[StateValue, JSONObject]:
        """Refuse missing/duplicate/foreign/sessionless records before interpreting any scalar."""
        evidence: JSONObject = {"field": name, "source": item.source, "received_at_ms": None}
        evidence["semantic"] = item.semantic
        if len(matches) != 1:
            return StateValue(
                "conflict" if matches else "unavailable", reason="状态记录缺失或原始来源重复"
            ), evidence
        record = matches[0]
        envelope = record.get("value")
        if not isinstance(envelope, dict) or envelope.get("payload_schema") != item.schema:
            return StateValue("unknown", reason="状态记录与登记格式不一致"), evidence
        received, ttl, epoch, sequence = (
            envelope.get(key)
            for key in ("received_at_ms", "valid_for_ms", "source_epoch", "sequence")
        )
        evidence.update(
            {
                "received_at_ms": received,
                "source_epoch": epoch,
                "sequence": sequence,
                "source_observed_at_ms": envelope.get("source_observed_at_ms"),
                "controller_observed_at_ms": now,
            }
        )
        if (
            type(received) is not int
            or received < 0
            or now < received
            or type(ttl) is not int
            or not 0 < ttl <= item.ttl
            or not isinstance(epoch, str)
            or not epoch
            or type(sequence) is not int
            or sequence <= 0
        ):
            return StateValue("unknown", reason="原始时钟、序号或会话依据不完整"), evidence
        retained = now - received + elapsed
        if record.get("stale") is not False or retained >= ttl:
            return StateValue("stale", reason="状态已过期，未作为当前值使用"), evidence
        nodes = cast(list[JSONValue], self.inventory["nodes"])
        owners = [
            node for node in nodes if isinstance(node, dict) and node.get("node_id") == item.node
        ]
        if len(owners) != 1 or owners[0].get("liveness") != "Reachable":
            return StateValue("unavailable", reason="上报节点不可达或可达观测不明确"), evidence
        value = envelope.get("value")
        if not isinstance(value, dict) or (
            item.profile
            and item.profile.object_class == "world"
            and value.get("physical_entity_id") != item.object["object_id"]
        ):
            return StateValue("unknown", reason="状态内容与原始对象身份不一致"), evidence
        assert item.profile is not None
        sample = item.profile.decode(value, name, retained)
        if sample.status == "reported" and (
            not math.isfinite(sample.remaining_ms) or sample.remaining_ms <= 0
        ):
            sample = StateValue("stale", reason="字段的原始有效期已用完")
        if sample.status == "reported":
            sample = StateValue(
                sample.status,
                sample.value,
                sample.unit,
                sample.reason,
                min(sample.remaining_ms, ttl - retained),
                sample.details,
            )
        evidence["complete"] = sample.status == "reported"
        return sample, evidence


def _render_field(name: str, sample: StateValue) -> str:
    """Present a concise human value while retaining Unknown and map/measurement qualifications."""
    title = LABELS.get(name, name)
    if sample.status != "reported":
        label = {"stale": "已过期", "conflict": "来源有歧义", "unsupported": "暂不支持"}.get(
            sample.status, "未知"
        )
        return f"{safe_text(title)}：{label}；{safe_text(sample.reason)}"
    if name == "position" and isinstance(sample.value, dict):
        value = sample.value
        return (
            f"位置：地图 {safe_text(str(value['map_id']))}，x={value['x']} m，y={value['y']} m，"
            f"朝向={value['yaw']} rad\n  报告位置；定位质量、原测量时间和共享坐标系未验证。"
        )
    text = {"standing": "站立", "down": "趴下"}.get(str(sample.value), str(sample.value))
    suffix = " " + safe_text(sample.unit) if sample.unit else ""
    return f"{safe_text(title)}：{safe_text(text)}{suffix}（上报值）"


def read_state(
    client: ReadOnlyController,
    target: str,
    requested: tuple[str, ...],
    seconds: float,
    profiles: tuple[StateProfile, ...] = DEFAULT_PROFILES,
) -> StateSnapshot:
    """Perform three fixed-origin GETs under one original deadline; retain no write authority."""
    target = identifier(target)
    requested = fields(list(requested))
    if isinstance(seconds, bool) or not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise WorkbenchError("状态读取预算无效。")
    started = time.monotonic()
    deadline = started + seconds
    timeout = client.timeout
    responses: list[JSONObject] = []
    try:
        for index in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkbenchError("状态读取时间已用完；当前状态未知。")
            client.timeout = min(timeout, remaining)
            if index == 0:
                responses.append(client.state_providers())
            elif index == 1:
                responses.append(client.inventory())
            else:
                declarations = _selected(exports(responses[0], profiles), target)
                semantics = {
                    item.semantic
                    for item in declarations
                    if item.profile and any(name in item.profile.fields for name in requested)
                }
                semantic = (
                    next(iter(semantics))
                    if len(semantics) == 1
                    else ""
                    if semantics
                    else "reported"
                )
                responses.append(client.state_records(semantic))
    finally:
        client.timeout = timeout
    if time.monotonic() >= deadline:
        raise WorkbenchError("状态读取时间已用完；当前状态未知。")
    return StateSnapshot(
        target,
        requested,
        responses[0],
        responses[1],
        responses[2],
        started,
        datetime.now(UTC).isoformat(),
        profiles,
    )
