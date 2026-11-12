"""Current Mission State projections against actual HTTP and focused source edge cases."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.mission_views import render_mission_list
from roboguide_workbench.routing import Query


def _record(
    mission_id: str, channel: str, value: JSONObject, *, stale: bool | None = None
) -> JSONObject:
    """Match the production built-in State envelope, including independent source identities."""
    lifecycle = channel == "mission-lifecycle"
    return {
        "object": {"class": "roboguide", "object_type": "mission", "object_id": mission_id},
        "channel_id": channel,
        "semantic": "derived" if lifecycle else "desired",
        "source": "roboguide:runtime-orchestration"
        if lifecycle
        else "roboguide:mission-orchestrator",
        "value": value,
        "stale": stale,
    }


def _state(*records: JSONObject) -> JSONObject:
    """Create one complete current State response without model-created facts."""
    return {"schema": "roboguide.state-query/v0.1", "observed_at_ms": 123, "records": list(records)}


@contextmanager
def _http_state(raw: JSONObject) -> Iterator[tuple[ReadOnlyController, list[str]]]:
    """Serve a real bounded State response through the production GET-only client."""
    calls: list[str] = []
    body = json.dumps(raw, ensure_ascii=False).encode()

    class Handler(BaseHTTPRequestHandler):
        """Return the exact fixture bytes without routing any Mission actions."""

        def do_GET(self) -> None:
            """Retain the actual requested source and tenant scope."""
            calls.append(self.path)
            assert self.headers["X-RoboGuide-Tenant"] == "test-tenant"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Keep the deterministic local fixture quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield (
            ReadOnlyController(f"http://127.0.0.1:{server.server_port}", tenant="test-tenant"),
            calls,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_active_projection_keeps_unknown_and_never_infers_from_terminal_task_rows() -> None:
    """Missing/conflicting runtime lifecycle is Unknown, while terminal missions stay terminal."""
    raw = _state(
        _record("running", "accepted-plan", {"objective": "巡检"}),
        _record("running", "mission-lifecycle", {"lifecycle": "Running"}),
        _record("done-with-active-task", "accepted-plan", {"objective": "旧任务"}),
        _record("done-with-active-task", "mission-lifecycle", {"lifecycle": "Completed"}),
        {
            "object": {"object_type": "task", "object_id": "task-1"},
            "channel_id": "task-lifecycle",
            "value": {"lifecycle": "Active"},
        },
        _record("missing-lifecycle", "accepted-plan", {"objective": "没有生命周期"}),
        _record("conflict", "accepted-plan", {"objective": "冲突"}),
        _record("conflict", "mission-lifecycle", {"lifecycle": "Running"}),
        _record("conflict", "mission-lifecycle", {"lifecycle": "Completed"}),
        _record("stale", "accepted-plan", {"objective": "过期"}),
        _record("stale", "mission-lifecycle", {"lifecycle": "Running"}, stale=True),
    )
    text, complete, ids = render_mission_list(raw, active_only=True)
    assert complete is False
    assert ids == ["running", "missing-lifecycle", "conflict", "stale"]
    assert "Running=1" in text and "Completed=1" in text and "Unknown=3" in text
    assert "当前活动 Mission：1 个；另有状态未知：3 个" in text
    assert "done-with-active-task" not in text
    assert "生命周期冲突：Running/Completed" in text
    assert "不能据此断言运行状态" in text


@pytest.mark.parametrize("plan", [None, {"objective": 17}, {"objective": "过期目标"}])
def test_missing_or_invalid_plan_metadata_never_erases_known_terminal_lifecycle(
    plan: JSONObject | None,
) -> None:
    """A current runtime Completed is authoritative independently of an optional objective."""
    records = [_record("done", "mission-lifecycle", {"lifecycle": "Completed"})]
    if plan is not None:
        records.append(_record("done", "accepted-plan", plan, stale=True))
    text, complete, ids = render_mission_list(_state(*records), active_only=True)
    assert complete is True and ids == []
    assert "Completed=1" in text and "Unknown=0" in text and "当前活动 Mission：0 个" in text
    all_text, _, all_ids = render_mission_list(_state(*records))
    assert all_ids == ["done"] and "done：Completed" in all_text
    assert "目标未提供" in all_text


def test_unrelated_channels_and_sources_cannot_change_original_runtime_state() -> None:
    """Generic State exports remain separate from the built-in Mission lifecycle authority."""
    source_record = _record("one", "mission-lifecycle", {"lifecycle": "Running"})
    source_record["source"] = "node:unrelated/sensor"
    class_record = _record("one", "mission-lifecycle", {"lifecycle": "Running"})
    cast(JSONObject, class_record["object"])["class"] = "world"
    text, complete, ids = render_mission_list(
        _state(
            _record("one", "accepted-plan", {"objective": "原始目标"}),
            _record("one", "mission-lifecycle", {"lifecycle": "Completed"}),
            _record("one", "accepted-plan", {"objective": "冲突目标"}),
            {
                "object": {"class": "roboguide", "object_type": "mission", "object_id": "one"},
                "channel_id": "custom-extra",
                "source": "roboguide:other",
                "value": {"anything": True},
            },
            source_record,
            class_record,
        )
    )
    assert complete is True and ids == ["one"]
    assert "one：Completed" in text and "Unknown=0" in text
    assert "目标存在冲突" in text and "未作为 Mission 生命周期依据" in text


@pytest.mark.parametrize("bad", [{}, {"object": None}, {"object": {"object_id": "lost"}}])
def test_malformed_objects_cannot_report_complete_empty_active_cohort(bad: JSONObject) -> None:
    """Unidentifiable source rows make the read partial instead of proving no running work."""
    text, complete, ids = render_mission_list(_state(bad), active_only=True)
    assert complete is False and ids == []
    assert "不能排除未识别的活动任务" in text
    assert "当前响应没有 Running" not in text


def test_large_http_projection_counts_full_cohort_and_bounds_only_display_rows() -> None:
    """Hundreds of long current objectives survive HTTP and stay within the model evidence cap."""
    records: list[JSONObject] = []
    for index in range(350):
        mission_id = f"mission-{index:03d}"
        records.extend(
            [
                _record(mission_id, "accepted-plan", {"objective": "长目标" * 300}),
                _record(
                    mission_id,
                    "mission-lifecycle",
                    {"lifecycle": "Running" if index % 2 else "Completed"},
                ),
            ]
        )
    with _http_state(_state(*records)) as (client, calls):
        text, complete, ids = render_mission_list(client.missions(), active_only=True)
    assert calls == ["/v1/state/records?object_type=mission"]
    assert complete is True and len(text.encode()) < 24_000
    assert 0 < len(ids) <= 128
    assert "350 个" in text and "Running=175" in text and "Completed=175" in text
    assert "当前活动 Mission：175 个" in text and "完整数量仍为上方聚合" in text
    assert "Controller State 观察时间：123 ms" in text
    assert all(int(mission_id.rsplit("-", 1)[1]) % 2 for mission_id in ids)


def test_duplicate_identical_lifecycle_records_count_one_mission() -> None:
    """Repeated same-source values count once; conflicting runtime values remain Unknown."""
    record = _record("same", "mission-lifecycle", {"lifecycle": "Running"})
    text, complete, ids = render_mission_list(_state(record, record), active_only=True)
    assert complete is True and ids == ["same"]
    assert "Running=1" in text and "Unknown=0" in text


def test_active_missions_is_a_selector_free_closed_query() -> None:
    """The natural active intent cannot smuggle an ID, ordinal or free-form message."""
    descriptor: JSONObject = {
        "kind": "active_missions",
        "value": None,
        "index": None,
        "message": None,
    }
    assert Query.parse(descriptor).kind == "active_missions"
    for field_name, value in (("value", "mission"), ("index", 1), ("message", "do more")):
        with pytest.raises(WorkbenchError):
            Query.parse({**descriptor, field_name: value})


@pytest.mark.parametrize("field_name", ["class", "source", "semantic"])
@pytest.mark.parametrize("channel", ["accepted-plan", "mission-lifecycle"])
@pytest.mark.parametrize("missing_value", ["absent", "null"])
def test_missing_canonical_attribution_keeps_id_unknown_and_partial(
    field_name: str,
    channel: str,
    missing_value: str,
) -> None:
    """No missing built-in source field can turn a plausible lifecycle into current Running."""
    bad = _record("unqualified", channel, {"lifecycle": "Running", "objective": "目标"})
    owner = cast(JSONObject, bad["object"]) if field_name == "class" else bad
    if missing_value == "absent":
        owner.pop(field_name)
    else:
        owner[field_name] = None
    records = [bad]
    if channel == "accepted-plan":
        records.append(_record("unqualified", "mission-lifecycle", {"lifecycle": "Running"}))
    else:
        records.append(_record("unqualified", "accepted-plan", {"objective": "目标"}))
    text, complete, ids = render_mission_list(_state(*records), active_only=True)
    assert complete is False and ids == ["unqualified"]
    if channel == "mission-lifecycle":
        assert "Running=0" in text and "Unknown=1" in text
        assert "unqualified：Unknown（缺少来源身份）" in text
    else:
        assert "Running=1" in text and "Unknown=0" in text
        assert "unqualified：Running" in text
    assert "当前响应没有 Running" not in text
