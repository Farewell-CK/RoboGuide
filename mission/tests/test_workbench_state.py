"""Generic declared State queries, original sample freshness and versioned field extensions."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import cast

import pytest
from mission.models import JSONObject, JSONValue
from roboguide_workbench import state_queries
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.state_profiles import StateProfile, StateValue
from roboguide_workbench.state_queries import StateSnapshot, discover, fields, read_state
from test_workbench import inventory, node
from test_workbench_v2 import ObservedController

PHYSICAL = "roboguide.world.robot-physical-observation/v0.1"


def declaration(*, legacy: bool = False) -> JSONObject:
    """Match the actual Controller provider-catalog serialization of one Node State export."""
    return {
        "export_id": "robot-state" if legacy else "physical-observation",
        "local_system_id": "robot-observer" if legacy else "physical-observer",
        "object": {
            "class": "node" if legacy else "world",
            "object_type": "robot" if legacy else "robot-physical-observation",
            "object_id": "dog2" if legacy else "entity-dog2",
        },
        "semantic": "reported",
        "payload_schema": "lite3.robot-state/v0.1" if legacy else PHYSICAL,
        "valid_for_ms": 5000,
    }


def physical_value() -> JSONObject:
    """Retain independent battery/posture windows and qualified Mapping response age."""
    return {
        "schema_version": PHYSICAL,
        "physical_entity_id": "entity-dog2",
        "posture": {
            "availability": "reported_current",
            "reported_posture": "standing",
            "aggregate_age_ms": 100,
            "source_stale_ms": 5000,
            "remaining_at_publisher_ms": 4900,
        },
        "battery": {
            "availability": "reported_current",
            "percent": 82,
            "remaining_at_publisher_ms": 4900,
        },
        "mapping": {
            "response_availability": "reported_current_response",
            "map_id": "warehouse",
            "mode": "localization",
            "reported_pose": {"x": 1.25, "y": -2.5, "theta": 0.4},
            "response_age_ms": 100,
            "pose_measurement_time": None,
            "localization_quality": None,
            "map_revision_id": None,
            "localization_verified": False,
        },
    }


def legacy_value() -> JSONObject:
    """Keep the already-supported local robot-state diagnostic profile unchanged."""
    return {
        "chassis": {
            "contract_id": "robonix/primitive/chassis/state",
            "availability": "current",
            "age_ms": 100,
            "value": {
                "stale": False,
                "stale_ms": 5000,
                "posture": "down",
                "battery": {"percent": 65},
            },
        },
        "mapping": {
            "availability": "current",
            "age_ms": 100,
            "value": {
                "has_map": True,
                "mode": "localization",
                "map_id": "warehouse",
                "pose": {"x": 1.25, "y": -2.5, "theta": 0.4},
            },
        },
    }


def fixture(*, legacy: bool = False) -> tuple[JSONObject, JSONObject, JSONObject]:
    """Return exact declared ownership, complete inventory and a source-attributed State record."""
    export = declaration(legacy=legacy)
    catalog: JSONObject = {
        "schema": "roboguide.state-provider-catalog/v0.1",
        "built_in": [],
        "nodes": [{"node_id": "dog2", "state_exports": [export]}],
    }
    state: JSONObject = {
        "schema": "roboguide.state-query/v0.1",
        "observed_at_ms": 1000,
        "records": [
            {
                "object": export["object"],
                "semantic": "reported",
                "source": "node:dog2/" + cast(str, export["local_system_id"]),
                "channel_id": export["export_id"],
                "stale": False,
                "value": {
                    "payload_schema": export["payload_schema"],
                    "received_at_ms": 900,
                    "valid_for_ms": 5000,
                    "source_observed_at_ms": 90_000_000,
                    "source_epoch": "original-session",
                    "sequence": 7,
                    "value": legacy_value() if legacy else physical_value(),
                },
            }
        ],
    }
    return catalog, inventory(node("dog2")), state


class StateController(ObservedController):
    """Expose only synthetic GET fixtures while retaining original HTTP-budget observations."""

    def __init__(self, *, legacy: bool = False) -> None:
        """Keep mutable adversarial inputs separate from each actual returned snapshot."""
        super().__init__()
        self.catalog, self.current_inventory, self.current_state = fixture(legacy=legacy)
        self.timeouts: list[float] = []

    def state_providers(self) -> JSONObject:
        """Read one complete current registration facade without mutating the source."""
        self.reads.append("providers")
        self.timeouts.append(self.timeout)
        return copy.deepcopy(self.catalog)

    def inventory(self) -> JSONObject:
        """Read current synthetic liveness without claiming a physical device observation."""
        self.reads.append("inventory")
        self.timeouts.append(self.timeout)
        return copy.deepcopy(self.current_inventory)

    def state_records(self, semantic: str = "reported") -> JSONObject:
        """Read original immutable source envelopes independently of later display consumption."""
        self.reads.append("state")
        self.timeouts.append(self.timeout)
        return copy.deepcopy(self.current_state)


def snapshot(monkeypatch: pytest.MonkeyPatch, *, legacy: bool = False) -> StateSnapshot:
    """Freeze a virtual local clock without replacing Python's process-global time module."""
    monkeypatch.setattr(state_queries, "time", SimpleNamespace(monotonic=lambda: 100.0))
    catalog, current_inventory, state = fixture(legacy=legacy)
    return StateSnapshot(
        "dog2",
        ("battery", "position", "posture"),
        catalog,
        current_inventory,
        state,
        100.0,
        "fixture-read-time",
    )


def payload(read: StateSnapshot) -> JSONObject:
    """Access the original schema payload for a precisely scoped adversarial mutation."""
    row = cast(list[JSONObject], read.state["records"])[0]
    return cast(JSONObject, cast(JSONObject, row["value"])["value"])


@pytest.mark.parametrize("legacy", [False, True])
def test_declared_profiles_use_the_same_state_chain_and_preserve_raw_source(
    monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    """Two existing schemas return scalar, unit and map-qualified facts without producer imports."""
    read = snapshot(monkeypatch, legacy=legacy)
    original = copy.deepcopy(read.state)
    result = read.render()
    assert result.complete and all(row["complete"] is True for row in result.components)
    assert f"电量：{65 if legacy else 82} %" in result.text and "warehouse" in result.text
    assert "朝向=0.4 rad" in result.text and ("趴下" if legacy else "站立") in result.text
    assert "未验证" in result.text and read.state == original
    assert result.components[1]["details"] == {
        "localization_verified": False,
        "shared_frame_verified": False,
        "pose_measurement_time": None,
        "qualification": "报告位置；原定位测量时间、定位质量和共享坐标系未验证",
    }


@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "expired",
        "rollback",
        "epoch",
        "sequence",
        "schema",
        "entity",
        "owner",
        "duplicate",
        "offline",
    ],
)
def test_unqualified_records_never_return_current_values(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Missing, foreign, expired, unreachable and conflicting evidence stays explicitly unknown."""
    read = snapshot(monkeypatch)
    rows = cast(list[JSONObject], read.state["records"])
    row, envelope = rows[0], cast(JSONObject, rows[0]["value"])
    if change == "stale":
        row["stale"] = True
    elif change == "expired":
        envelope["valid_for_ms"] = 50
    elif change == "rollback":
        envelope["received_at_ms"] = 1001
    elif change == "epoch":
        envelope["source_epoch"] = None
    elif change == "sequence":
        envelope["sequence"] = True
    elif change == "schema":
        envelope["payload_schema"] = "other/v1"
    elif change == "entity":
        payload(read)["physical_entity_id"] = "other-device"
    elif change == "owner":
        row["source"] = "node:other/physical-observer"
    elif change == "duplicate":
        rows.append(copy.deepcopy(row))
    else:
        cast(list[JSONObject], read.inventory["nodes"])[0]["liveness"] = "Unreachable"
    result = read.render()
    assert not result.complete and all(item["value"] is None for item in result.components)
    assert "电量：82" not in result.text and "x=1.25" not in result.text


@pytest.mark.parametrize("value", [None, True, -1, 101, float("nan"), float("inf"), 10**1000])
def test_invalid_battery_is_independent_from_other_qualified_fields(
    monkeypatch: pytest.MonkeyPatch, value: JSONValue
) -> None:
    """A missing or invalid percentage cannot erase valid pose/posture or become zero charge."""
    read = snapshot(monkeypatch)
    cast(JSONObject, payload(read)["battery"])["percent"] = value
    result = read.render()
    assert not result.complete and result.components[0]["value"] is None
    assert result.components[1]["complete"] is True and result.components[2]["complete"] is True
    assert "电量：未知" in result.text


@pytest.mark.parametrize("kind", ["native-window", "retention", "local-latency"])
def test_nested_samples_expire_without_an_outer_heartbeat_refresh(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Native age, Controller retention and subsequent display time consume the same sample."""
    read = snapshot(monkeypatch)
    if kind == "native-window":
        cast(JSONObject, payload(read)["posture"])["aggregate_age_ms"] = 6000
    elif kind == "retention":
        cast(JSONObject, cast(list[JSONObject], read.state["records"])[0]["value"])[
            "received_at_ms"
        ] = 0
        cast(JSONObject, payload(read)["battery"])["remaining_at_publisher_ms"] = 500
    else:
        assert read.render().complete
        monkeypatch.setattr(state_queries, "time", SimpleNamespace(monotonic=lambda: 106.0))
    result = read.render()
    assert not result.complete and result.components[0]["value"] is None


def test_conflicting_registered_objects_do_not_select_the_first_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Node's two physical objects remain ambiguous even if one looks current or values agree."""
    read = snapshot(monkeypatch)
    declarations = cast(
        list[JSONObject], cast(list[JSONObject], read.catalog["nodes"])[0]["state_exports"]
    )
    other = copy.deepcopy(declarations[0])
    other["export_id"] = "second-physical-observation"
    cast(JSONObject, other["object"])["object_id"] = "entity-other"
    declarations.append(other)
    result = read.render()
    assert not result.complete and all(row["status"] == "conflict" for row in result.components)
    assert "来源有歧义" in result.text


def test_discovery_preserves_unknown_schemas_without_fabricating_fields() -> None:
    """An unknown format is discoverable as metadata but cannot be interpreted by a model."""
    catalog, _, _ = fixture()
    item = cast(list[JSONObject], cast(list[JSONObject], catalog["nodes"])[0]["state_exports"])[0]
    item["payload_schema"] = "vendor.new-state/v9"
    result, targets = discover(catalog)
    assert result.complete and targets == ["entity-dog2"]
    assert "尚未接入解释器" in result.text and "battery" not in result.text


@pytest.mark.parametrize(
    "kind", ["version", "duplicate-node", "duplicate-export", "object", "ttl", "missing"]
)
def test_corrupt_registration_never_lends_state_coverage(kind: str) -> None:
    """Incomplete catalogs and duplicate original declarations refuse before field discovery."""
    catalog, _, _ = fixture()
    nodes = cast(list[JSONObject], catalog["nodes"])
    declarations = cast(list[JSONObject], nodes[0]["state_exports"])
    if kind == "version":
        catalog["schema"] = "roboguide.state-provider-catalog/v9"
    elif kind == "duplicate-node":
        nodes.append(copy.deepcopy(nodes[0]))
    elif kind == "duplicate-export":
        other = copy.deepcopy(declarations[0])
        other["local_system_id"] = "other-owner"
        declarations.append(other)
    elif kind == "object":
        declarations[0]["object"] = {"class": "world"}
    elif kind == "ttl":
        declarations[0]["valid_for_ms"] = True
    else:
        nodes[0].pop("state_exports")
    with pytest.raises(WorkbenchError):
        discover(catalog)


@pytest.mark.parametrize(
    "kind",
    [
        "bad-coordinate",
        "map-missing",
        "wrong-mode",
        "old-response",
        "invented-localization",
        "inner-version",
    ],
)
def test_position_keeps_original_map_and_qualification_gaps(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """An active map or fresh wrapper never supplies invalid coordinates or localization proof."""
    read = snapshot(monkeypatch)
    original = payload(read)
    mapping = cast(JSONObject, original["mapping"])
    if kind == "bad-coordinate":
        cast(JSONObject, mapping["reported_pose"])["x"] = True
    elif kind == "map-missing":
        mapping["map_id"] = None
    elif kind == "wrong-mode":
        mapping["mode"] = "mapping"
    elif kind == "old-response":
        mapping["response_age_ms"] = 9999
    elif kind == "invented-localization":
        mapping["localization_verified"] = True
    else:
        original["schema_version"] = "roboguide.world.robot-physical-observation/v9"
    result = read.render()
    assert result.components[1]["value"] is None and not result.complete
    assert "x=1.25" not in result.text


def test_unknown_posture_does_not_erase_independent_battery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid current battery observation remains usable beside unknown posture."""
    read = snapshot(monkeypatch)
    posture = cast(JSONObject, payload(read)["posture"])
    posture["availability"], posture["reported_posture"] = "unavailable", None
    result = read.render()
    assert not result.complete and result.components[0]["complete"] is True
    assert result.components[2]["value"] is None and "82 %" in result.text


def test_state_directory_only_publishes_whole_visible_targets() -> None:
    """Long declarations cannot lend unseen identities to later ordinal queries."""
    catalog, _, _ = fixture()
    declarations = cast(
        list[JSONObject], cast(list[JSONObject], catalog["nodes"])[0]["state_exports"]
    )
    original = copy.deepcopy(declarations[0])
    declarations.clear()
    for index in range(100):
        export = copy.deepcopy(original)
        export["export_id"] = f"export-{index}"
        cast(JSONObject, export["object"])["object_id"] = f"entity-{index:03}-" + "x" * 220
        declarations.append(export)
    result, targets = discover(catalog)
    assert 0 < len(targets) < 64 and len(result.text.encode()) < 22_000
    assert "截断" in result.text
    assert all(target in result.text for target in targets)


def test_new_schema_extension_does_not_require_a_new_chat_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A maintained field profile extends discovery and reading through the same query substrate."""
    read = snapshot(monkeypatch, legacy=True)

    def temperature(value: JSONObject, _name: str, retained: float) -> StateValue:
        """Interpret the fixture's exact scalar under its original outer receipt window."""
        return StateValue("reported", value["temperature"], "°C", remaining_ms=5000 - retained)

    profile = StateProfile("fixture.temperature/v1", "node", "robot", ("temperature",), temperature)
    declaration_ = cast(
        list[JSONObject], cast(list[JSONObject], read.catalog["nodes"])[0]["state_exports"]
    )[0]
    declaration_["payload_schema"] = profile.schema
    envelope = cast(JSONObject, cast(list[JSONObject], read.state["records"])[0]["value"])
    envelope["payload_schema"], envelope["value"] = profile.schema, {"temperature": 23.5}
    extended = StateSnapshot(
        "dog2",
        ("temperature",),
        read.catalog,
        read.inventory,
        read.state,
        read.started,
        read.received_at,
        (profile,),
    )
    result = extended.render()
    assert result.complete and "23.5 °C" in result.text
    discovered, _ = discover(read.catalog, "dog2", (profile,))
    assert "temperature" in discovered.text


def test_actual_read_cost_and_timeouts_keep_one_original_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each fixed GET consumes the same deadline and restores the caller's original timeout."""
    clock = [100.0]
    monkeypatch.setattr(state_queries, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = StateController()
    original = client.inventory

    def inventory_after_delay() -> JSONObject:
        """Spend part of the already-admitted turn before the third read is configured."""
        clock[0] += 0.2
        return original()

    monkeypatch.setattr(client, "inventory", inventory_after_delay)
    read = read_state(client, "dog2", fields(["电量"]), 0.5)
    assert client.reads == ["providers", "inventory", "state"]
    assert client.timeout == 5 and client.timeouts[-1] < client.timeouts[0] <= 0.5
    assert read.render().complete


def test_expired_state_read_stops_before_the_next_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow prior GET cannot renew the original turn and admit another source request."""
    clock = [100.0]
    monkeypatch.setattr(state_queries, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    client = StateController()
    original = client.inventory

    def delayed_inventory() -> JSONObject:
        """Consume the original budget after the actual second read was admitted."""
        result = original()
        clock[0] += 0.6
        return result

    monkeypatch.setattr(client, "inventory", delayed_inventory)
    with pytest.raises(WorkbenchError, match="时间已用完"):
        read_state(client, "dog2", ("battery",), 0.5)
    assert client.reads == ["providers", "inventory"] and client.timeout == 5


@pytest.mark.parametrize("selected", [[], ["battery", "电量"], ["x"] * 9])
def test_invalid_field_lists_fail_before_query(selected: list[str]) -> None:
    """Empty, duplicate or unbounded field selections never enter a Controller GET."""
    with pytest.raises(WorkbenchError):
        fields(selected)


def test_declared_observed_channel_preserves_its_original_semantic() -> None:
    """Discovery may select observed data without reclassifying it as a reported source."""
    client = StateController(legacy=True)
    declaration_ = cast(
        list[JSONObject], cast(list[JSONObject], client.catalog["nodes"])[0]["state_exports"]
    )[0]
    declaration_["semantic"] = "observed"
    cast(list[JSONObject], client.current_state["records"])[0]["semantic"] = "observed"
    result = read_state(client, "dog2", ("battery",), 5).render()
    assert result.complete and result.components[0]["semantic"] == "observed"
