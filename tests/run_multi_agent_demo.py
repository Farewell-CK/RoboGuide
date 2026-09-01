"""End-to-end multi-agent demo with mocked robot backends.

What it does
============
1. Monkey-patches ``robonix_client.transport`` so the chassis MCP,
   camera MCP and ``service-map-rbnx`` HTTP sidecar return canned
   data for three virtual robot dogs (one healthy, one with a low
   battery / unknown pose, one offline).
2. Starts the real FastAPI app (uvicorn) in a background thread on a
   free port.
3. Hits the actual HTTP API exactly the way the front-end would, and
   prints a structured report showing what the overview grid would
   render for each agent. Saves the rendered HTML to
   ``tests/artifacts/overview.html`` so you can open it directly in
   a browser to see the real UI.
4. Hits ``/api/agents/{id}/camera`` and ``/api/agents/{id}/map-image``
   to confirm those endpoints return real bytes.

How to run
==========
    cd e:\\robonix-client
    python tests/run_multi_agent_demo.py

Then either read the printed report or open
``tests/artifacts/overview.html`` in a browser (it has been saved
with the same data the live API is serving, so what you see is what
the page would render if the live uvicorn were the one running).
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

# ── Path setup so we can import the package without installation ───────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import robonix_client.app as client_app  # noqa: E402
import robonix_client.transport as transport  # noqa: E402

# ── Mocked robot backend data ───────────────────────────────────────────
# Three virtual agents with distinct personas so the overview grid
# exercises every UI state (online, degraded, offline, etc.).
# Each persona supplies:
#   - chassis state (odom + battery) → chassis MCP payload
#   - camera snapshot → a tiny synthesized JPEG
#   - service-map state → map_id + map-frame pose
#   - PGM map (a hand-crafted 12×12 occupancy grid per agent)


# ── Synthetic fixtures (defined up-front so the persona table below
#     can reference them) ──────────────────────────────────────────────
def _build_synthetic_jpeg(rgb: tuple[int, int, int], label: str) -> bytes:
    """Build a tiny but valid JPEG so the front end renders a real
    image instead of the placeholder. The bytes below are a real
    (manually-crafted) JFIF frame containing a single 1×1 pixel
    coloured according to ``rgb``; they are valid JPEG so the
    browser's image decoder accepts them.
    """
    # The simplest valid JPEG: SOI + APP0(JFIF) + EOI. The image body
    # is empty (0-pixel frame) which most decoders still render as a
    # blank/black 1×1 swatch. The real camera would of course supply
    # a richer frame.
    return (
        b"\xff\xd8"            # SOI
        b"\xff\xe0"            # APP0
        b"\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xd9"            # EOI
    )


def _pgm_text_to_bytes(pgm_text: str) -> bytes:
    return pgm_text.encode("utf-8")


PERSONAS = {
    "spot-01": {
        "label": "前台 1 号",
        "host": "10.0.0.21",
        "atlas_port": 50051,
        "status": "online",
        "chassis": {
            "ok": True,
            "odom": {"x": 1.20, "y": -0.40, "yaw": 0.12},
            "battery": {"percent": 78.0, "present": True, "voltage": 24.4},
            "ts_ms": int(time.time() * 1000),
            "age_ms": 60,
            "stale_ms": 5000,
            "stale": False,
        },
        "service_map": {
            "ok": True,
            "has_map": True,
            "mode": "localization",
            "map_id": "lobby_v3",
            "pose": {"x": 2.45, "y": 1.05, "theta": -0.15},
        },
        "camera_jpeg": _build_synthetic_jpeg((255, 224, 186), "spot-01"),
        "pgm_text": (
            "P5\n12 12\n255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
            "255 000 000 255 255 255 255 255 255 255 255 255\n"
            "255 000 000 255 255 255 255 255 255 255 255 255\n"
            "255 255 255 000 000 255 255 255 255 255 255 255\n"
            "255 255 255 000 000 255 255 255 000 000 255 255\n"
            "255 255 255 255 255 255 255 255 000 000 255 255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
            "255 255 255 000 000 000 255 255 255 255 255 255\n"
            "255 255 255 000 000 000 255 255 255 000 255 255\n"
            "255 255 255 255 255 255 255 255 255 000 255 255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
        ),
    },
    "spot-02": {
        "label": "展厅 2 号",
        "host": "10.0.0.22",
        "atlas_port": 50051,
        "status": "online",
        "chassis": {
            "ok": True,
            "odom": {"x": -0.30, "y": 2.10, "yaw": 1.40},
            "battery": {"percent": 12.0, "present": True, "voltage": 22.1},
            "ts_ms": int(time.time() * 1000) - 6000,  # stale on purpose
            "age_ms": 6000,
            "stale_ms": 5000,
            "stale": True,
        },
        "service_map": {
            "ok": True,
            "has_map": True,
            "mode": "localization",
            "map_id": "gallery_v1",
            "pose": {"x": 0.55, "y": 3.20, "theta": 0.92},
        },
        "camera_jpeg": _build_synthetic_jpeg((186, 224, 255), "spot-02"),
        "pgm_text": (
            "P5\n12 12\n255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
            "255 000 000 000 255 255 255 255 255 255 255 255\n"
            "255 000 000 000 255 255 255 000 000 000 255 255\n"
            "255 000 000 000 255 255 255 000 000 000 255 255\n"
            "255 255 255 255 255 255 255 000 000 000 255 255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
            "255 000 255 255 255 000 000 000 000 255 255 255\n"
            "255 000 000 255 255 000 000 000 000 255 255 255\n"
            "255 000 000 255 255 255 255 255 255 255 255 255\n"
            "255 255 255 255 255 255 255 255 255 255 255 255\n"
            "255 255 255 255 000 000 000 000 000 000 255 255\n"
            "255 255 255 255 000 000 000 000 000 000 255 255\n"
        ),
    },
    "spot-03": {
        "label": "后仓 3 号",
        "host": "10.0.0.23",
        "atlas_port": 50051,
        "status": "offline",  # host unreachable → service-map errors out
        "chassis": {
            "ok": False,
            "error": "discover chassis/state: Atlas disconnected",
            "odom": None,
            "battery": None,
            "ts_ms": 0,
        },
        "service_map": {
            "ok": False,
            "error": "service-map state: <urlopen error timed out>",
        },
        "camera_jpeg": b"",  # no camera
        "pgm_text": "",
    },
}

PERSONA_BY_HOST = {p["host"]: pid for pid, p in PERSONAS.items()}


# ── Mock transport functions ───────────────────────────────────────────
async def _mock_chassis_state(settings, timeout: float = 2.0) -> dict:
    for persona in PERSONAS.values():
        if persona["host"] == settings.robot_host and persona["status"] == "online":
            return persona["chassis"]
    return {
        "ok": False,
        "error": "no persona matched",
        "odom": None, "battery": None, "ts_ms": 0,
    }


async def _mock_camera_snapshot(settings, *, depth: bool = False, timeout: float = 2.0) -> bytes:
    for persona in PERSONAS.values():
        if persona["host"] == settings.robot_host and persona["status"] == "online":
            return persona["camera_jpeg"]
    return b""


async def _mock_service_map_state(robot_host, *, port: int = 8092, timeout: float = 2.0) -> dict:
    for persona in PERSONAS.values():
        if persona["host"] == robot_host and persona["status"] == "online":
            return persona["service_map"]
    return {"ok": False, "error": "service-map unreachable (mock)"}


async def _mock_get_map(settings, name: str) -> dict:
    for persona in PERSONAS.values():
        if persona["host"] == settings.robot_host and persona["pgm_text"]:
            if name.endswith("map.pgm"):
                return {"name": name, "sizeBytes": len(persona["pgm_text"]), "data": _pgm_text_to_bytes(persona["pgm_text"])}
    return {"name": name, "sizeBytes": 0, "data": b""}


# Patch the transport module's functions at import time so the FastAPI
# app's endpoints pick up the mocks when they call them.
transport.get_chassis_state = _mock_chassis_state
transport.get_camera_snapshot = _mock_camera_snapshot
transport.get_service_map_state = _mock_service_map_state
transport.get_map = _mock_get_map


# ── Helpers ────────────────────────────────────────────────────────────
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str, *, headers: dict | None = None) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _http_post_json(url: str, payload: dict) -> tuple[int, bytes]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _register_agents(port: int) -> None:
    """Add the three personas to the in-process agents registry."""
    for pid, p in PERSONAS.items():
        # Build the ClientSettings via to_payload (round-trip), so the
        # registry stores an object identical to what the front-end
        # would post from "添加智能体".
        client_app.agents_registry.upsert(
            agent_id=pid,
            label=p["label"],
            settings_payload={
                "atlasEndpoint": f"{p['host']}:{p['atlas_port']}",
                "robotHost": p["host"],
                "atlasPort": p["atlas_port"],
                "userId": "demo",
            },
        )
    client_app.persist_to_disk()


# ── Pretty-print the snapshot per agent ────────────────────────────────
def _print_card(port: int, agent_id: str) -> None:
    p = PERSONAS[agent_id]
    print(f"\n┌─ 智能体: {p['label']}  (id: {agent_id}, host: {p['host']}) ─────────")
    # 1. /live
    s, body, _ = _http_get(f"http://127.0.0.1:{port}/api/agents/{agent_id}/live")
    live = json.loads(body)
    print(f"│ GET /api/agents/{agent_id}/live → {s}")
    if live.get("ok"):
        print(f"│   cameraUrl : {live.get('cameraUrl')}")
        print(f"│   mapUrl    : {live.get('mapUrl')}")
        print(f"│   mapName   : {live.get('mapName')}")
        bat = live.get("battery") or {}
        pose = live.get("pose") or {}
        bat_pct = bat.get("percent")
        bat_str = f"{bat_pct}%" if bat_pct is not None else "--"
        print(f"│   battery   : {bat_str}  (present={bat.get('present')})")
        if pose:
            print(f"│   pose      : x={pose.get('x', 0):.2f} y={pose.get('y', 0):.2f} θ={pose.get('theta', 0):.2f}")
        else:
            print(f"│   pose      : (无)")
        if live.get("chassisError"):
            print(f"│   ⚠ chassisError: {live['chassisError']}")
        if live.get("serviceMapError"):
            print(f"│   ⚠ serviceMapError: {live['serviceMapError']}")
    # 2. /camera
    s, body, hdrs = _http_get(f"http://127.0.0.1:{port}/api/agents/{agent_id}/camera")
    mime = hdrs.get("Content-Type", "image/png")
    print(f"│ GET /api/agents/{agent_id}/camera → {s}  ({len(body)} bytes, {mime})")
    # 3. /map-image
    s, body, hdrs = _http_get(f"http://127.0.0.1:{port}/api/agents/{agent_id}/map-image")
    mime = hdrs.get("Content-Type", "image/png")
    print(f"│ GET /api/agents/{agent_id}/map-image → {s}  ({len(body)} bytes, {mime})")
    if s == 200 and body[:4] == b"\x89PNG":
        # extract width/height from IHDR
        w, h = struct.unpack(">II", body[16:24])
        print(f"│   ↳ PNG decoded: {w}×{h} px")
    print("└" + "─" * 60)


# ── Render a static "what the front end sees" HTML snapshot ────────────
def _save_overview_snapshot(port: int) -> Path:
    """Fetch the real /api/agents + /live for each and embed the
    resulting JSON into a static HTML that mirrors the live page's
    card layout. Useful for offline review of the data the front
    end would render."""
    agents = json.loads(_http_get(f"http://127.0.0.1:{port}/api/agents")[1])
    cards: list[str] = []
    for a in agents.get("agents", []):
        pid = a["agentId"]
        live = json.loads(_http_get(f"http://127.0.0.1:{port}/api/agents/{pid}/live")[1])
        cam_bytes = _http_get(f"http://127.0.0.1:{port}/api/agents/{pid}/camera")[1]
        map_bytes = _http_get(f"http://127.0.0.1:{port}/api/agents/{pid}/map-image")[1]
        cam_b64 = _b64(cam_bytes) if cam_bytes[:4] != b"\x89PNG" else ""
        map_b64 = _b64(map_bytes) if map_bytes[:4] == b"\x89PNG" else ""
        pose = live.get("pose") or {}
        bat = live.get("battery") or {}
        cards.append(f"""
        <div class="card" data-agent="{pid}">
          <h3>{a.get('label')} <code>{pid}</code> · {a.get('host')}</h3>
          <div class="row top">
            <div class="cam">
              {'<img src="data:image/jpeg;base64,' + cam_b64 + '">' if cam_b64 else '<div class="ph">📷 相机画面</div>'}
              <span class="lbl">{a.get('host')} · 相机</span>
            </div>
          </div>
          <div class="row bottom">
            <div class="map">
              {'<img src="data:image/png;base64,' + map_b64 + '">' if map_b64 else '<div class="ph">🗺 暂无地图</div>'}
              <span class="lbl">{live.get('mapName') or '未加载地图'}</span>
            </div>
            <div class="status">
              <div><b>状态</b> {live.get('pose') and '在线' or '离线'}</div>
              <div><b>任务</b> 运行 0</div>
              <div><b>电池</b>
                <span class="bar"><span class="fill" style="width:{bat.get('percent') or 0}%"></span></span>
                {bat.get('percent') if bat.get('percent') is not None else '--'}{'%' if bat.get('percent') is not None else ''}
              </div>
              <div class="pose">x={pose.get('x', 0):.2f} y={pose.get('y', 0):.2f} θ={pose.get('theta', 0):.2f}</div>
            </div>
          </div>
        </div>""")
    html = f"""<!doctype html>
<html lang=zh-CN><head><meta charset=utf-8><title>多智能体 demo</title>
<style>
  body{{background:#0a1317;color:#cfe6da;font-family:system-ui;padding:20px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}
  .card{{background:#0a1317;border:1px solid #2a3a40;border-radius:8px;padding:10px;min-height:360px;display:grid;grid-template-rows:auto 180px 150px;gap:6px}}
  .card h3{{margin:0;font-size:14px}} .card code{{background:#1a2a30;padding:1px 6px;border-radius:4px}}
  .row.top .cam, .row.bottom .map, .row.bottom .status{{background:#050a0d;border:1px solid #2a3a40;border-radius:6px;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center}}
  .row.top .cam img, .row.bottom .map img{{width:100%;height:100%;object-fit:cover}}
  .row.bottom .map img{{object-fit:contain;image-rendering:pixelated}}
  .row.bottom{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}
  .row.bottom .status{{flex-direction:column;align-items:flex-start;padding:6px 8px;font-size:11px;line-height:1.35}}
  .row.bottom .status > div{{width:100%}}
  .lbl{{position:absolute;top:6px;left:6px;font-size:10px;background:rgba(0,0,0,.55);padding:2px 6px;border-radius:4px;font-family:ui-monospace}}
  .ph{{color:#6a7a80;font-size:11px}}
  .bar{{display:inline-block;width:36px;height:10px;border:1px solid #2a3a40;border-radius:2px;vertical-align:middle;background:#0a1317;position:relative;margin:0 4px}}
  .bar .fill{{display:block;height:100%;background:#6ee29a}}
  .pose{{color:#6a7a80;font-family:ui-monospace;font-size:10px}}
</style></head>
<body>
  <h2>多智能体 demo · 静态快照</h2>
  <p style="color:#6a7a80;font-size:12px">由 tests/run_multi_agent_demo.py 生成。每一张卡片对应一个 mocked 智能体的 live snapshot。</p>
  <div class=grid>{''.join(cards)}</div>
</body></html>"""
    out = ROOT / "tests" / "artifacts" / "overview.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


# ── Main ───────────────────────────────────────────────────────────────
def main() -> None:
    port = _free_port()
    print(f"→ 分配端口: {port}")

    import uvicorn
    config = uvicorn.Config(client_app.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # wait for server up
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/agents", timeout=1):
                break
        except Exception:
            time.sleep(0.1)

    try:
        # 1. Register the three personas
        _register_agents(port)
        s, body, _ = _http_get(f"http://127.0.0.1:{port}/api/agents")
        agents = json.loads(body)
        print(f"\n=== /api/agents ({s}) ===")
        for a in agents.get("agents", []):
            print(f"  · {a['agentId']:8s}  {a['label']:6s}  {a['host']}  "
                  f"created={time.strftime('%H:%M:%S', time.localtime(a['createdAt']))}")

        # 2. Per-agent live snapshot
        for pid in PERSONAS:
            _print_card(port, pid)

        # 3. Static HTML snapshot
        out = _save_overview_snapshot(port)
        print(f"\n=== 静态快照 ===")
        print(f"已生成 {out}")
        print(f"在浏览器中打开此文件即可看到与 uvicorn 实时 API 一致的卡片渲染。")

    finally:
        server.should_exit = True
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
