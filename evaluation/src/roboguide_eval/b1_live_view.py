"""Loopback-only live operator view for one B1 run directory.

The viewer reads evaluation evidence and the latest already-rendered Habitat
frame.  It never calls Mission, Control, Node, EMOS, or Habitat and therefore
cannot advance or change the system under test.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_JSONL_RECORDS = 20
MAX_AGENT_HISTORIES = 32

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboGuide B1 Live Operator View</title>
<style>
body{margin:0;background:#111;color:#eee;font:14px/1.4 system-ui,sans-serif}
header{padding:12px 18px;background:#1d1d1d;position:sticky;top:0;z-index:2}
main{display:grid;grid-template-columns:minmax(520px,3fr) minmax(360px,2fr);gap:14px;padding:14px}
.panel{background:#1b1b1b;border:1px solid #383838;border-radius:8px;padding:12px;overflow:auto}
#frame{display:block;width:100%;height:auto;background:#000;min-height:280px;object-fit:contain}
h1,h2{margin:0 0 8px}h1{font-size:18px}h2{font-size:15px;color:#9fd3ff}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font:12px/1.45 ui-monospace,monospace}
.stack{display:grid;gap:14px}.muted{color:#aaa}.ok{color:#91e59c}.pending{color:#ffd479}
@media(max-width:1000px){main{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <h1>RoboGuide B1 Live Operator View</h1>
  <div id="phase" class="pending">starting</div>
</header>
<main>
  <section class="panel">
    <h2>Habitat: first- and third-person views for each configured agent</h2>
    <img id="frame" alt="Waiting for Habitat reset">
  </section>
  <section class="stack">
    <div class="panel">
      <h2>MI plan and Control assignment</h2>
      <pre id="mission">Waiting for evidence...</pre>
    </div>
    <div class="panel">
      <h2>Stage2 provider-visible dialogue and tool calls</h2>
      <div class="muted">
        Returned messages only; no hidden chain of thought is claimed.
      </div>
      <pre id="stage2">Waiting for Stage2...</pre>
    </div>
  </section>
</main>
<script>
async function refresh(){
  try{
    const response=await fetch('/api/status',{cache:'no-store'});
    const value=await response.json();
    document.getElementById('phase').textContent=value.phase;
    document.getElementById('phase').className=value.phase==='completed'?'ok':'pending';
    document.getElementById('mission').textContent=JSON.stringify({
      request:value.request,plan:value.plan,mission:value.mission,
      assignments:value.assignments
    },null,2);
    document.getElementById('stage2').textContent=JSON.stringify({
      actions:value.stage2_actions,chat_history:value.chat_history
    },null,2);
    if(value.frame.available){
      document.getElementById('frame').src='/api/frame?t='+Date.now();
    }
  }catch(error){
    document.getElementById('phase').textContent=
      'viewer backend unavailable; showing the last received archive state';
    document.getElementById('phase').className='pending';
  }
}
refresh();setInterval(refresh,500);
</script>
</body>
</html>
"""


def _read_json(path: Path) -> Any | None:
    """Read one bounded JSON file, returning unavailable during partial writes."""
    try:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            return {"unavailable": "evidence file exceeds live-view byte bound"}
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _tail_jsonl(path: Path) -> list[Any]:
    """Return a bounded suffix of valid JSONL records without blocking writers."""
    try:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            return [{"unavailable": "evidence file exceeds live-view byte bound"}]
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_JSONL_RECORDS:]
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return []
    records: list[Any] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _request_summary(record: Any) -> dict[str, Any] | None:
    """Project one Mission Request record to operator-relevant lifecycle fields."""
    if not isinstance(record, dict):
        return None
    return {
        key: record.get(key)
        for key in ("request_id", "mission_id", "lifecycle", "issues", "review_history")
        if key in record
    }


def _read_chat_histories(chat_root: Path) -> dict[str, Any]:
    """Discover bounded per-agent Stage2 histories without assuming agent cardinality."""
    try:
        candidates = sorted(
            path
            for path in chat_root.iterdir()
            if path.is_file() and path.name.endswith("_action_history.json")
        )
    except OSError:
        return {}
    histories = {
        path.name.removesuffix("_action_history.json"): _read_json(path)
        for path in candidates[:MAX_AGENT_HISTORIES]
    }
    if len(candidates) > MAX_AGENT_HISTORIES:
        histories["_collection"] = {
            "unavailable": "agent history count exceeds live-view bound",
            "discovered": len(candidates),
            "included": MAX_AGENT_HISTORIES,
        }
    return histories


def build_snapshot(run_dir: Path) -> dict[str, Any]:
    """Build one read-only snapshot from files already emitted by the run."""
    frozen_input = _read_json(run_dir / "b1-input-used.json")
    request_record = _read_json(run_dir / "b1-request-record.json")
    mission = _read_json(run_dir / "mission.json")
    verdict = _read_json(run_dir / "b1-verdict.json")
    frame_status = _read_json(run_dir / "live" / "latest-frame.json")
    plan = request_record.get("plan") if isinstance(request_record, dict) else None
    episode_id = (
        str(frozen_input.get("episode_id"))
        if isinstance(frozen_input, dict) and frozen_input.get("episode_id") is not None
        else "unavailable"
    )
    chat_root = run_dir / "evidence" / "chat-history" / episode_id
    chat_history = _read_chat_histories(chat_root)
    if isinstance(verdict, dict):
        phase = "completed"
    elif isinstance(mission, dict):
        phase = f"mission: {mission.get('status', 'running')}"
    elif isinstance(request_record, dict):
        phase = f"mission intelligence: {request_record.get('lifecycle', 'running')}"
    elif (run_dir / "b1-timing.txt").exists():
        phase = "mission intelligence: provider call in progress"
    else:
        phase = "starting services"
    return {
        "assignments": _tail_jsonl(run_dir / "evidence" / "assignment-arrival.jsonl"),
        "chat_history": chat_history,
        "frame": {
            "available": (run_dir / "live" / "latest-frame.jpg").is_file(),
            "status": frame_status,
        },
        "frozen_input": frozen_input,
        "mission": mission,
        "phase": phase,
        "plan": plan,
        "request": _request_summary(request_record),
        "stage2_actions": _tail_jsonl(run_dir / "evidence" / "stage2-actions.jsonl"),
        "verdict": verdict,
    }


class B1LiveViewServer(ThreadingHTTPServer):
    """Serve a bounded read-only projection for one immutable run directory."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], run_dir: Path) -> None:
        """Bind one loopback address and retain the selected run directory."""
        self.run_dir = run_dir
        super().__init__(address, B1LiveViewHandler)


class B1LiveViewHandler(BaseHTTPRequestHandler):
    """Serve the live HTML, JSON snapshot, and latest JPEG only."""

    server: B1LiveViewServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        """Handle one bounded read-only live-view request."""
        route = urlsplit(self.path).path
        if route == "/":
            self._respond(HTTPStatus.OK, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if route == "/healthz":
            self._respond(HTTPStatus.OK, b'{"status":"ok"}\n', "application/json")
            return
        if route == "/api/status":
            payload = json.dumps(
                build_snapshot(self.server.run_dir),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self._respond(HTTPStatus.OK, payload, "application/json")
            return
        if route == "/api/frame":
            self._serve_frame()
            return
        self._respond(HTTPStatus.NOT_FOUND, b'{"error":"route not found"}\n', "application/json")

    def log_message(self, message: str, *args: Any) -> None:
        """Suppress per-refresh access noise in the experiment log."""
        del message, args

    def _serve_frame(self) -> None:
        """Serve only the atomically published latest operator JPEG."""
        path = self.server.run_dir / "live" / "latest-frame.jpg"
        try:
            if path.stat().st_size > MAX_EVIDENCE_BYTES:
                raise OSError("frame exceeds live-view byte bound")
            payload = path.read_bytes()
        except (FileNotFoundError, OSError):
            self._respond(
                HTTPStatus.SERVICE_UNAVAILABLE,
                b'{"error":"frame unavailable"}\n',
                "application/json",
            )
            return
        self._respond(HTTPStatus.OK, payload, "image/jpeg")

    def _respond(self, status: HTTPStatus, payload: bytes, content_type: str) -> None:
        """Write one no-cache response with an exact length."""
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def _arguments() -> argparse.Namespace:
    """Parse fixed loopback viewer settings."""
    parser = argparse.ArgumentParser(description="Serve one B1 run's live operator evidence")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28110)
    return parser.parse_args()


def main() -> None:
    """Run the read-only viewer until the parent experiment stops it."""
    arguments = _arguments()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("the B1 live viewer must bind a loopback host")
    arguments.run_dir.mkdir(parents=True, exist_ok=True)
    server = B1LiveViewServer((arguments.host, arguments.port), arguments.run_dir.resolve())
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
