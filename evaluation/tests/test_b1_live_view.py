"""Deterministic tests for the read-only B1 operator viewer."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from roboguide_eval.b1_live_view import B1LiveViewServer, build_snapshot


def _write_json(path: Path, value: object) -> None:
    """Write one deterministic JSON fixture with parent creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_snapshot_tracks_generic_episode_and_provider_visible_stage2(tmp_path: Path) -> None:
    """The viewer follows the frozen episode without hard-coded Episode51 paths."""
    _write_json(tmp_path / "b1-input-used.json", {"episode_id": "73"})
    _write_json(
        tmp_path / "b1-request-record.json",
        {
            "request_id": "request-1",
            "mission_id": "mission-1",
            "lifecycle": "Accepted",
            "plan": {"tasks": [{"id": "task-1"}]},
        },
    )
    for agent_index in range(4):
        _write_json(
            tmp_path / f"evidence/chat-history/73/agent_{agent_index}_action_history.json",
            [
                {
                    "role": "assistant",
                    "tool_calls": [{"name": "nav_to_obj", "agent": agent_index}],
                }
            ],
        )
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "stage2-actions.jsonl").write_text(
        json.dumps({"sequence": 1, "selected_action": {"name": "nav_to_obj"}}) + "\n",
        encoding="utf-8",
    )
    snapshot = build_snapshot(tmp_path)
    assert snapshot["phase"] == "mission intelligence: Accepted"
    assert snapshot["plan"] == {"tasks": [{"id": "task-1"}]}
    assert snapshot["chat_history"]["agent_0"][0]["role"] == "assistant"
    assert list(snapshot["chat_history"]) == [
        "agent_0",
        "agent_1",
        "agent_2",
        "agent_3",
    ]
    assert snapshot["chat_history"]["agent_3"][0]["tool_calls"][0]["agent"] == 3
    assert snapshot["stage2_actions"][0]["sequence"] == 1


def test_http_view_serves_only_bounded_status_and_latest_frame(tmp_path: Path) -> None:
    """The loopback server exposes its page, snapshot, and one latest JPEG."""
    _write_json(tmp_path / "b1-input-used.json", {"episode_id": "51"})
    frame = tmp_path / "live/latest-frame.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpeg-evidence")
    server = B1LiveViewServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{endpoint}/healthz", timeout=2) as response:  # noqa: S310
            assert response.status == 200
        with urllib.request.urlopen(f"{endpoint}/api/status", timeout=2) as response:  # noqa: S310
            assert json.loads(response.read())["frame"]["available"] is True
        with urllib.request.urlopen(f"{endpoint}/api/frame", timeout=2) as response:  # noqa: S310
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.read() == b"jpeg-evidence"
        try:
            urllib.request.urlopen(f"{endpoint}/provider-config.toml", timeout=2)  # noqa: S310
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("the viewer exposed an unregistered run file")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
