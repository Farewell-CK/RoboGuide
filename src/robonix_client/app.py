from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import grpc
import yaml
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import audio_server_control
from .agents import (
    DEFAULT_AGENT_ID,
    hydrate_from_disk,
    new_agent_id,
    persist_to_disk,
    registry as agents_registry,
)
from .audio_reverse_bridge import AudioReverseBridge
from .vitals_api import router as vitals_router
from . import transport
from .transport import (
    DEFAULT_ATLAS,
    ClientSettings,
    abort_turn,
    delete_from_shared,
    delete_map,
    deploy_from_shared_to_robot,
    discover_audio_bridge,
    enroll_voiceprint,
    finish_voice_capture,
    get_camera_snapshot,
    get_chassis_state,
    get_handsfree_status,
    get_service_map_state,
    list_active_plans,
    list_audio_devices,
    list_audio_providers,
    list_maps,
    list_shared_library,
    play_tts_test,
    pcm16_stats,
    pull_from_robot_to_shared,
    record_pcm,
    select_audio_device,
    shared_maps_root,
    start_voice_session,
    set_handsfree_enabled,
    submit_text,
    voice_finish_supported,
    watch_handsfree_events,
    system_snapshot,
)

STATIC_DIR = Path(__file__).with_name("static")

app = FastAPI(title="Robonix Client", version="0.1.0")


class _RevalidatingStaticFiles(StaticFiles):
    """Serve the UI assets with `Cache-Control: no-cache`.

    Without it the browser is free to reuse a cached app.js for as long as it
    likes, so a rebuilt UI keeps showing the previous build until someone
    thinks to hard-reload -- the failure looks like the change never landed.
    `no-cache` still allows caching; it just forces the ETag revalidation that
    turns an unchanged asset into a 304, so the only cost is one conditional
    request per asset per load.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", _RevalidatingStaticFiles(directory=STATIC_DIR), name="static")
app.include_router(vitals_router)
_reverse_audio: AudioReverseBridge | None = None
SETTINGS_PATH = Path(
    os.environ.get(
        "ROBONIX_CLIENT_SETTINGS",
        Path.home() / ".config" / "robonix-client" / "settings.yaml",
    )
).expanduser()
PERSISTED_SETTING_KEYS = {
    "robotHost", "atlasPort", "liaisonEndpoint", "userId", "recordSeconds",
    "language", "micNodeId", "micDeviceId", "speakerNodeId",
    "speakerDeviceId", "ttsNodeId", "enrollUserId", "enrollUserName",
}


def _split_default_atlas(raw: str) -> tuple[str, int]:
    target = (raw or DEFAULT_ATLAS).strip()
    parsed = urlparse(target if "://" in target else f"grpc://{target}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 50051
    return host, port


class AudioServerStartRequest(BaseModel):
    host: str = audio_server_control.DEFAULT_BRIDGE_BIND_HOST
    port: int = audio_server_control.DEFAULT_BRIDGE_PORT
    uiHost: str = audio_server_control.DEFAULT_UI_HOST


class EnrollRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    userId: str
    userName: str = ""
    seconds: float = 6.0


class AudioPlayTestRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    text: str = "Robonix speaker test"


class AudioMicTestRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    seconds: float = 1.0


class AudioReverseConnectRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    providerId: str


class HandsfreeSetRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    enabled: bool


class ClientSettingsRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""


class AudioProviderDevicesRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    providerId: str


class AudioRouteApplyRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""


def _payload_steer(payload: dict[str, Any]) -> bool:
    return bool(payload.get("steer") or payload.get("interactionMode") == "steer")


def _payload_expected_turn_id(payload: dict[str, Any]) -> str:
    return str(payload.get("expectedTurnId") or "").strip()


def _load_persisted_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    raw = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"settings file must contain a mapping: {SETTINGS_PATH}")
    return {key: raw[key] for key in PERSISTED_SETTING_KEYS if key in raw}


def _save_persisted_settings(settings: dict[str, Any]) -> dict[str, Any]:
    selected = {key: settings[key] for key in PERSISTED_SETTING_KEYS if key in settings}
    ClientSettings.from_payload(selected)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_PATH.with_suffix(f"{SETTINGS_PATH.suffix}.tmp")
    temporary.write_text(
        yaml.safe_dump(selected, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(SETTINGS_PATH)
    # Mirror the legacy write into the default agent so the new
    # multi-agent sidebar stays in sync even when callers still
    # use the legacy /api/settings endpoint.
    try:
        agents_registry.update_legacy(selected)
        persist_to_disk()
    except Exception:
        pass
    return selected


# ── Agent helpers ──────────────────────────────────────────────────
# Settings payloads may include ``agentId`` on top of the legacy
# ``settings`` blob; the helper picks the right ``ClientSettings``
# to use for a request. Missing or unknown agent ids fall back to
# the default agent so a partially-migrated front end keeps working.
def _settings_for(agent_id: str | None, payload: dict[str, Any] | None) -> ClientSettings:
    payload = payload or {}
    if agent_id:
        try:
            return agents_registry.resolve_settings(agent_id)
        except Exception:
            pass
    # Last-resort: build a fresh ClientSettings from the inline blob.
    # This mirrors the legacy code path that pre-dated the registry.
    try:
        return ClientSettings.from_payload(payload)
    except Exception:
        return agents_registry.resolve_settings(None)


def _serialise_agent(rec) -> dict[str, Any]:
    return rec.to_public_dict()


def _serialise_agent_with_settings(rec) -> dict[str, Any]:
    return {**_serialise_agent(rec), "settings": rec.settings.to_payload()}


# ── Agents API ─────────────────────────────────────────────────────
@app.get("/api/agents")
async def agents_list() -> dict[str, Any]:
    return {
        "ok": True,
        "defaultAgentId": DEFAULT_AGENT_ID,
        "agents": [_serialise_agent(r) for r in agents_registry.list()],
    }


class AgentUpsertRequest(BaseModel):
    agentId: str = ""
    label: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/agents")
async def agents_upsert(req: AgentUpsertRequest) -> dict[str, Any]:
    try:
        agent_id = (req.agentId or "").strip() or new_agent_id()
        rec = agents_registry.upsert(agent_id, req.label, req.settings)
        persist_to_disk()
        return {"ok": True, "agent": _serialise_agent_with_settings(rec)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class AgentPatchRequest(BaseModel):
    label: str = ""


@app.patch("/api/agents/{agent_id}")
async def agents_patch(agent_id: str, req: AgentPatchRequest) -> dict[str, Any]:
    try:
        rec = agents_registry.rename(agent_id, req.label)
        if rec is None:
            return {"ok": False, "error": f"agent {agent_id!r} not found"}
        persist_to_disk()
        return {"ok": True, "agent": _serialise_agent(rec)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.delete("/api/agents/{agent_id}")
async def agents_delete(agent_id: str) -> dict[str, Any]:
    if agent_id == DEFAULT_AGENT_ID:
        return {
            "ok": False,
            "error": "cannot remove the default agent; clear its settings instead",
        }
    removed = agents_registry.remove(agent_id)
    if not removed:
        return {"ok": False, "error": f"agent {agent_id!r} not found"}
    persist_to_disk()
    return {"ok": True}


# ── Agent "live" endpoints ─────────────────────────────────────────
# These power the small camera / map / pose / battery tiles in the
# overview grid. They fan out to three backends in parallel:
#   1. `transport.get_chassis_state`  — pose + battery (gRPC MCP)
#   2. `transport.get_camera_snapshot` — JPEG frame (gRPC MCP)
#   3. `transport.get_service_map_state` — current map_id + map-frame
#      pose from the `service-map-rbnx` sidecar (HTTP, port 8092)
# All three are best-effort: failures degrade to empty payloads, so
# the overview card falls back to placeholders without crashing the
# rest of the snapshot.
TRANSPARENT_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


def _resolve_agent_settings(agent_id: str) -> tuple[Any, ClientSettings | None]:
    """Return the registry record + ClientSettings for an agent, or
    (None, None) if not found. Centralised so the live endpoints all
    hit the same lookup logic."""
    rec = agents_registry.get(agent_id)
    if rec is None:
        return None, None
    return rec, rec.settings


@app.get("/api/agents/{agent_id}/live")
async def agents_live(agent_id: str) -> dict[str, Any]:
    """Aggregate per-agent live signals for the overview card.

    Each field is best-effort: if the source is unavailable the
    corresponding value is left as ``None`` so the front end can fall
    back to a placeholder without crashing the whole card.
    """
    rec, settings = _resolve_agent_settings(agent_id)
    if rec is None:
        return {"ok": False, "error": f"agent {agent_id!r} not found"}
    # The snapshot URLs use an agent-scoped version (`?v=`) so the
    # browser cache busts whenever the agent record changes; the
    # timestamp at the front end (see app.js) handles periodic refresh
    # of the same agent.
    camera_url = f"/api/agents/{agent_id}/camera"
    map_url = f"/api/agents/{agent_id}/map-image"
    payload: dict[str, Any] = {
        "ok": True,
        "agentId": agent_id,
        "cameraUrl": camera_url,
        "mapUrl": map_url,
        "mapName": "",
        "pose": None,
        "battery": None,
        "atlasEndpoint": settings.atlas_endpoint,
        "robotHost": settings.robot_host,
    }
    if not settings.robot_host:
        return payload
    # Fan out to all three backends in parallel. Each is wrapped in
    # its own try/except so a single failure does not poison the
    # whole snapshot.
    tasks: dict[str, Any] = {}
    if settings.atlas_endpoint:
        tasks["chassis"] = asyncio.create_task(transport.get_chassis_state(settings))
    if settings.robot_host:
        tasks["service_map"] = asyncio.create_task(
            transport.get_service_map_state(settings.robot_host)
        )
    if tasks:
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue
            if key == "chassis":
                if result.get("ok"):
                    payload["battery"] = result.get("battery")
                    # Chassis MCP gives odom-frame pose; we keep it as
                    # a fallback if service-map has nothing to offer.
                    payload["pose"] = result.get("odom")
                else:
                    payload["chassisError"] = result.get("error", "")
            elif key == "service_map":
                if result.get("ok"):
                    payload["mapName"] = result.get("map_name") or result.get("map_id")
                    # service-map-rbnx gives map-frame pose — preferred
                    # over the chassis MCP's odom-frame pose for the
                    # mini-map.
                    pose = result.get("pose")
                    if pose:
                        payload["pose"] = pose
                else:
                    payload["serviceMapError"] = result.get("error", "")
    return payload


@app.get("/api/agents/{agent_id}/camera")
async def agents_camera(agent_id: str) -> Response:
    """Return a single JPEG/PNG frame from the robot's camera.

    The endpoint calls the `robonix/primitive/camera/snapshot` MCP via
    gRPC, decodes the `sensor_msgs/Image.data` field, and serves the
    raw bytes with the matching MIME type. The front end appends a
    `?t=<ts>` cache-bust parameter; combined with FastAPI's default
    no-store semantics, each request re-fetches a fresh frame.
    """
    rec, settings = _resolve_agent_settings(agent_id)
    if rec is None or settings is None:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=404)
    if not settings.atlas_endpoint:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    jpeg = await transport.get_camera_snapshot(settings)
    if not jpeg:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    # `camera/snapshot` typically returns JPEG bytes; PNGs are also
    # possible if the camera primitive chooses to encode that way.
    media_type = "image/jpeg"
    if jpeg[:4] == b"\x89PNG":
        media_type = "image/png"
    return Response(
        content=jpeg,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


# ── PGM / PNG helpers for the mini-map tile ───────────────────────────
# service-map-rbnx stores the current map as a PGM (occupancy grid)
# in `<DEPLOY_ROOT>/rbnx-boot/cache/service-map-rbnx/maps/<map_id>/`.
# The `lite3_map_browser.map/get` MCP can pull any file out, so we:
#   1. ask service-map for the current map_id,
#   2. fetch <map_id>/map.pgm via map/get,
#   3. fetch <map_id>/map.yaml (optional) for resolution/origin,
#   4. convert to a PNG that the front end can drop straight into
#      an <img>.
_PGM_CACHED: dict[str, tuple[float, bytes]] = {}
_PGM_CACHE_TTL_S = 2.0  # avoid re-fetching the same map on every poll

def _pgm_to_png(pgm: bytes) -> bytes | None:
    """Decode a PGM (P5 binary or P2 ASCII) to PNG bytes.

    Pure stdlib (no PIL dependency) so the client runs in the
    minimum-dependency environment. Returns None on parse failure.
    """
    import io
    import struct
    if not pgm or len(pgm) < 10:
        return None
    # Tokenise header
    pos = 0
    header_tokens: list[bytes] = []
    comment = False
    while len(header_tokens) < 4 and pos < len(pgm):
        if comment:
            nl = pgm.find(b"\n", pos)
            if nl == -1:
                return None
            pos = nl + 1
            comment = False
            continue
        # skip whitespace
        while pos < len(pgm) and pgm[pos : pos + 1] in (b" ", b"\t", b"\r", b"\n"):
            pos += 1
        if pos >= len(pgm):
            return None
        if pgm[pos : pos + 1] == b"#":
            comment = True
            continue
        # scan token
        start = pos
        while pos < len(pgm) and pgm[pos : pos + 1] not in (b" ", b"\t", b"\r", b"\n", b"#"):
            pos += 1
        header_tokens.append(pgm[start:pos])
    if len(header_tokens) < 3:
        return None
    magic = header_tokens[0]
    try:
        width = int(header_tokens[1])
        height = int(header_tokens[2])
    except ValueError:
        return None
    if magic not in (b"P5", b"P2"):
        return None
    max_val = 255
    if len(header_tokens) >= 4:
        try:
            max_val = int(header_tokens[3])
        except ValueError:
            max_val = 255
    # Skip exactly one whitespace separator after the max value (P5)
    if pos < len(pgm) and pgm[pos : pos + 1] in (b" ", b"\t", b"\r", b"\n"):
        pos += 1
    elif pos < len(pgm):
        # Tokeniser stopped at the next token, no separator — fine for P2
        pass
    if magic == b"P5":
        if len(pgm) - pos < width * height:
            return None
        pixels = pgm[pos : pos + width * height]
    else:
        # P2: ASCII pixel values, separated by whitespace
        data_text = pgm[pos:]
        tokens = data_text.split()
        if len(tokens) < width * height:
            return None
        try:
            pixels = bytes(int(t) for t in tokens[: width * height])
        except ValueError:
            return None
    # Occupancy grid convention: 0 = occupied (black), 255 = free
    # (white), -1 (204) = unknown (grey). Invert to a typical map
    # look: free=light grey, occupied=dark, unknown=mid grey.
    out = bytearray(width * height * 3)
    for i in range(width * height):
        v = pixels[i]
        if v >= 250:
            r = g = b = 245
        elif v <= 10:
            r = g = b = 40
        elif v == 205:  # unknown
            r = g = b = 150
        else:
            r = g = b = int(245 - (245 - 40) * (v / 255.0))
        out[i * 3] = r
        out[i * 3 + 1] = g
        out[i * 3 + 2] = b
    # Encode as PNG (filter type 0, 8-bit RGB)
    def _png_chunk(tag: bytes, data: bytes) -> bytes:
        import zlib
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )
    def _bead(arr: bytearray) -> bytearray:
        # Add a leading filter byte (0 = None) per scanline
        out2 = bytearray()
        for y in range(height):
            out2.append(0)
            out2.extend(arr[y * width * 3 : (y + 1) * width * 3])
        return out2
    import zlib
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(_bead(out)), 6)
    iend = b""
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", iend)


@app.get("/api/agents/{agent_id}/map-image")
async def agents_map_image(agent_id: str) -> Response:
    """Return the current map image for the robot.

    Resolution order:
      1. cache the last successful fetch for 2 s
      2. ask `service-map-rbnx` for the current `map_id` (port 8092)
      3. fetch `<map_id>/map.pgm` via `robonix/primitive/map/get`
      4. convert to PNG via pure-stdlib PGM decoder
    """
    rec, settings = _resolve_agent_settings(agent_id)
    if rec is None or settings is None:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=404)
    if not settings.robot_host or not settings.atlas_endpoint:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    # 1. cache
    loop = asyncio.get_event_loop()
    now = loop.time()
    cache_key = f"{agent_id}"
    cached = _PGM_CACHED.get(cache_key)
    if cached and (now - cached[0]) < _PGM_CACHE_TTL_S:
        return Response(content=cached[1], media_type="image/png", headers={"Cache-Control": "no-store"})
    # 2. current map id
    sm = await transport.get_service_map_state(settings.robot_host)
    if not sm.get("ok") or not sm.get("map_id"):
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    map_id = sm["map_id"]
    # 3. fetch the pgm
    try:
        file_resp = await transport.get_map(settings, f"{map_id}/map.pgm")
    except Exception:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    if not file_resp.get("data"):
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    # 4. decode
    png = _pgm_to_png(file_resp["data"])
    if not png:
        return Response(content=TRANSPARENT_PNG_1X1, media_type="image/png", status_code=503)
    _PGM_CACHED[cache_key] = (now, png)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


# ── Model name API (LLM model currently in use) ──────────────────
_MODEL_PATH = Path(
    os.environ.get(
        "ROBONIX_CLIENT_MODEL_FILE",
        Path.home() / ".config" / "robonix-client" / "model.txt",
    )
).expanduser()


def _read_model_name() -> str:
    try:
        if _MODEL_PATH.exists():
            value = _MODEL_PATH.read_text(encoding="utf-8").strip()
            if value:
                return value
    except Exception:
        pass
    return os.environ.get("ROBONIX_CLIENT_LLM_MODEL", "").strip()


def _write_model_name(name: str) -> str:
    cleaned = (name or "").strip()
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if cleaned:
        _MODEL_PATH.write_text(cleaned + "\n", encoding="utf-8")
    elif _MODEL_PATH.exists():
        _MODEL_PATH.unlink()
    return cleaned


@app.get("/api/model")
async def model_get() -> dict[str, Any]:
    return {
        "ok": True,
        "model": _read_model_name(),
        "path": str(_MODEL_PATH),
    }


class ModelSetRequest(BaseModel):
    model: str = ""


@app.put("/api/model")
async def model_set(req: ModelSetRequest) -> dict[str, Any]:
    name = _write_model_name(req.model)
    return {"ok": True, "model": name, "path": str(_MODEL_PATH)}


@app.on_event("startup")
async def start_client_audio() -> None:
    """Start local device I/O. The robot endpoint is Atlas-discovered later.

    Also hydrate the in-memory agent registry from disk so the first
    request to ``/api/agents`` reflects the persisted state (the
    legacy single-agent install gets a ``default`` entry seeded from
    the same ``settings.yaml``).
    """
    try:
        hydrate_from_disk()
    except Exception:
        # A corrupt settings file must not prevent the UI from
        # booting — the user can re-register agents from scratch.
        pass
    if os.environ.get("ROBONIX_CLIENT_REVERSE_AUDIO", "1").lower() in {"0", "false", "no"}:
        return
    audio_server_control.start()


@app.on_event("shutdown")
async def stop_client_audio() -> None:
    global _reverse_audio
    try:
        try:
            persist_to_disk()
        except Exception:
            pass
        if _reverse_audio is not None:
            _reverse_audio.stop()
    finally:
        _reverse_audio = None
        audio_server_control.stop()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/defaults")
async def defaults() -> dict[str, Any]:
    atlas_endpoint = os.environ.get("ROBONIX_ATLAS_ENDPOINT", DEFAULT_ATLAS)
    robot_host, atlas_port = _split_default_atlas(atlas_endpoint)
    launch_overrides = []
    for key in (
        "ROBONIX_ROBOT_HOST",
        "ROBONIX_ATLAS_PORT",
        "ROBONIX_CLIENT_USER_ID",
        "ROBONIX_CLIENT_SESSION_ID",
        "ROBONIX_CLIENT_SESSION_TITLE",
        "ROBONIX_CLIENT_MIC_NODE_ID",
        "ROBONIX_CLIENT_MIC_DEVICE_ID",
        "ROBONIX_CLIENT_SPEAKER_NODE_ID",
        "ROBONIX_CLIENT_SPEAKER_DEVICE_ID",
        "ROBONIX_CLIENT_TTS_NODE_ID",
    ):
        if os.environ.get(key):
            launch_overrides.append(key)
    return {
        "atlasEndpoint": atlas_endpoint,
        "robotHost": os.environ.get("ROBONIX_ROBOT_HOST", robot_host),
        "atlasPort": int(os.environ.get("ROBONIX_ATLAS_PORT", str(atlas_port))),
        "liaisonEndpoint": os.environ.get("ROBONIX_LIAISON_ENDPOINT", ""),
        "userId": os.environ.get("ROBONIX_CLIENT_USER_ID", ""),
        "sessionId": os.environ.get("ROBONIX_CLIENT_SESSION_ID", ""),
        "sessionTitle": os.environ.get("ROBONIX_CLIENT_SESSION_TITLE", ""),
        "micNodeId": os.environ.get("ROBONIX_CLIENT_MIC_NODE_ID", ""),
        "micDeviceId": os.environ.get("ROBONIX_CLIENT_MIC_DEVICE_ID", ""),
        "speakerNodeId": os.environ.get("ROBONIX_CLIENT_SPEAKER_NODE_ID", ""),
        "speakerDeviceId": os.environ.get("ROBONIX_CLIENT_SPEAKER_DEVICE_ID", ""),
        "ttsNodeId": os.environ.get("ROBONIX_CLIENT_TTS_NODE_ID", ""),
        "recordSeconds": int(os.environ.get("ROBONIX_CLIENT_RECORD_SECONDS", "30")),
        "audioServer": {
            "host": audio_server_control.DEFAULT_BRIDGE_HOST,
            "bindHost": audio_server_control.DEFAULT_BRIDGE_BIND_HOST,
            "port": audio_server_control.DEFAULT_BRIDGE_PORT,
            "uiHost": audio_server_control.DEFAULT_UI_HOST,
        },
        "launchOverrides": launch_overrides,
    }


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    try:
        return {
            "ok": True,
            "settings": _load_persisted_settings(),
            "path": str(SETTINGS_PATH),
        }
    except Exception as exc:
        return {
            "ok": False,
            "settings": {},
            "path": str(SETTINGS_PATH),
            "error": str(exc),
        }


@app.put("/api/settings")
async def put_settings(req: ClientSettingsRequest) -> dict[str, Any]:
    try:
        settings = _save_persisted_settings(req.settings)
        return {"ok": True, "settings": settings, "path": str(SETTINGS_PATH)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(SETTINGS_PATH)}


@app.get("/api/system")
async def system(
    atlas: str = Query(DEFAULT_ATLAS),
    agentId: str = Query(""),
) -> dict[str, Any]:
    try:
        # When an agent id is supplied the registry owns the
        # authoritative atlas endpoint; the legacy ``atlas`` query
        # parameter is only used as an override (or as a hint for
        # un-registered agents). The transport layer then
        # short-circuits the discovery if the endpoint is the
        # ``atlas`` placeholder from a partial config.
        target = atlas
        if agentId:
            try:
                target = agents_registry.resolve_settings(agentId).atlas_endpoint
            except Exception:
                pass
        return await system_snapshot(target)
    except Exception as exc:
        return {
            "atlasEndpoint": target,
            "summary": {"providers": 0, "active": 0, "errors": 1, "terminated": 0, "state": "offline"},
            "requiredContracts": [],
            "providers": [],
            "error": str(exc),
        }


@app.post("/api/executor/active-plans")
async def executor_active_plans(req: ClientSettingsRequest) -> dict[str, Any]:
    try:
        return await list_active_plans(_settings_for(req.agentId, req.settings))
    except Exception as exc:
        return {"available": False, "count": 0, "plans": [], "error": str(exc)}


@app.post("/api/maps/list")
async def maps_list(req: ClientSettingsRequest) -> dict[str, Any]:
    """List files in the robot's `robonix/primitive/map/list` cache.

    Mirrors the error contract used by `/api/executor/active-plans`:
    on failure the response has `available: false` plus a human-readable
    `error` field. The Maps page renders the file list and surface any
    failure message in the page header.
    """
    try:
        return await list_maps(_settings_for(req.agentId, req.settings))
    except Exception as exc:
        return {
            "available": False,
            "mapsDir": "",
            "count": 0,
            "files": [],
            "error": str(exc),
        }


@app.get("/api/maps/shared")
async def maps_shared_list() -> dict[str, Any]:
    """List the local shared map library (no robot required).

    Walks `<root>/<robot_id>/...`. The root defaults to
    `<repo>/maps` and can be overridden via `ROBONIX_SHARED_MAPS_DIR`.
    Always returns `ok: true`; failure to enumerate a particular
    directory is reflected in the absent entries (per-file `OSError`
    is caught and that file is simply skipped).
    """
    try:
        return {"ok": True, **list_shared_library()}
    except Exception as exc:
        return {
            "ok": False,
            "root": str(shared_maps_root()),
            "robots": [],
            "totalFiles": 0,
            "error": str(exc),
        }


class MapsSharedSyncRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    robot_id: str = ""


class MapsSharedDeployRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    robot_id: str = ""
    name: str = ""


class MapsSharedDeleteRequest(BaseModel):
    robot_id: str = ""
    name: str = ""


class MapsRobotDeleteRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)
    agentId: str = ""
    name: str = ""


@app.post("/api/maps/shared/sync")
async def maps_shared_sync(req: MapsSharedSyncRequest) -> dict[str, Any]:
    """Pull every file from the robot's cache into the shared library.

    `robot_id` defaults to the connected robot's host (the same value
    `ClientSettings.atlas_endpoint` uses) so the typical call from the
    UI is just `{ "settings": {...} }` without needing to know the host.
    """
    robot_id = (req.robot_id or "").strip()
    if not robot_id:
        try:
            settings = _settings_for(req.agentId, req.settings)
        except Exception as exc:
            return {"ok": False, "error": f"bad settings: {exc}"}
        robot_id = settings.robot_host or settings.atlas_host or ""
    if not robot_id:
        return {
            "ok": False,
            "error": "robot_id is required (set Robot Host or pass it explicitly)",
        }
    try:
        return await pull_from_robot_to_shared(
            _settings_for(req.agentId, req.settings), robot_id
        )
    except Exception as exc:
        return {
            "ok": False,
            "robotId": robot_id,
            "error": str(exc),
        }


@app.post("/api/maps/shared/deploy")
async def maps_shared_deploy(req: MapsSharedDeployRequest) -> dict[str, Any]:
    """Push a file from the shared library onto the connected robot.

    `robot_id` identifies which subdirectory of the shared library the
    file comes from (typically "the robot it was originally synced
    from"). The destination is the robot described by `settings`. The
    two are decoupled on purpose — a map synced from robot A can be
    pushed to robot B without copying through any temp space.
    """
    if not req.robot_id or not req.name:
        return {
            "ok": False,
            "error": "robot_id and name are required",
        }
    try:
        return await deploy_from_shared_to_robot(
            _settings_for(req.agentId, req.settings), req.robot_id, req.name
        )
    except Exception as exc:
        return {
            "ok": False,
            "sourceRobotId": req.robot_id,
            "name": req.name,
            "error": str(exc),
        }


@app.post("/api/maps/shared/delete")
async def maps_shared_delete(req: MapsSharedDeleteRequest) -> dict[str, Any]:
    """Delete a single file from the local shared library."""
    if not req.robot_id or not req.name:
        return {"ok": False, "error": "robot_id and name are required"}
    try:
        return delete_from_shared(req.robot_id, req.name)
    except Exception as exc:
        return {
            "ok": False,
            "robotId": req.robot_id,
            "name": req.name,
            "error": str(exc),
        }


@app.post("/api/maps/robot/delete")
async def maps_robot_delete(req: MapsRobotDeleteRequest) -> dict[str, Any]:
    """Delete a single file from the connected robot's map cache."""
    if not req.name:
        return {"ok": False, "error": "name is required"}
    try:
        result = await delete_map(
            _settings_for(req.agentId, req.settings), req.name
        )
        if not result.get("ok", True) and result.get("error"):
            return {"ok": False, "name": req.name, "error": result["error"]}
        return {"ok": True, "name": req.name, "robot": result}
    except Exception as exc:
        return {"ok": False, "name": req.name, "error": str(exc)}


@app.post("/api/voice/finish-supported")
async def voice_finish_supported_route(req: ClientSettingsRequest) -> dict[str, Any]:
    """Whether the connected liaison can accept a manual finish-capture request.

    Older liaisons never registered this capability, so callers should treat
    a False result as "not upgraded yet" and hide the finish-capture control,
    not as an error.
    """
    try:
        settings = _settings_for(req.agentId, req.settings)
        return {"supported": await voice_finish_supported(settings)}
    except Exception:
        return {"supported": False}


@app.post("/api/handsfree/set")
async def handsfree_set(req: HandsfreeSetRequest) -> dict[str, Any]:
    try:
        settings = _settings_for(req.agentId, req.settings)
        bridge = await _connect_selected_reverse_audio(settings) if req.enabled else None
        response = await set_handsfree_enabled(settings, req.enabled)
        if bridge is not None:
            response["audioBridge"] = bridge
        return response
    except Exception as exc:
        return {"available": False, "ok": False, "enabled": False, "state": "unavailable", "error": str(exc)}


@app.post("/api/handsfree/status")
async def handsfree_status(req: ClientSettingsRequest) -> dict[str, Any]:
    try:
        return await get_handsfree_status(_settings_for(req.agentId, req.settings))
    except Exception as exc:
        return {"available": False, "enabled": False, "state": "unavailable", "error": str(exc)}


@app.post("/api/audio-route/providers")
async def audio_route_providers(req: ClientSettingsRequest) -> dict[str, Any]:
    try:
        return await list_audio_providers(_settings_for(req.agentId, req.settings))
    except Exception as exc:
        return {"micProviders": [], "speakerProviders": [], "error": str(exc)}


@app.post("/api/audio-route/devices")
async def audio_route_devices(req: AudioProviderDevicesRequest) -> dict[str, Any]:
    try:
        return await list_audio_devices(_settings_for(req.agentId, req.settings), req.providerId)
    except Exception as exc:
        return {"providerId": req.providerId, "devices": [], "error": str(exc)}


@app.post("/api/audio-route/apply")
async def audio_route_apply(req: AudioRouteApplyRequest) -> dict[str, Any]:
    try:
        settings = _settings_for(req.agentId, req.settings)
        await _connect_selected_reverse_audio(settings)
        selected: list[dict[str, Any]] = []
        if settings.mic_node_id and settings.mic_device_id:
            selected.append(
                await select_audio_device(
                    settings,
                    settings.mic_node_id,
                    "input",
                    settings.mic_device_id,
                )
            )
        if settings.speaker_node_id and settings.speaker_device_id:
            selected.append(
                await select_audio_device(
                    settings,
                    settings.speaker_node_id,
                    "output",
                    settings.speaker_device_id,
                )
            )
        return {"ok": True, "selected": selected}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/audio-server/start")
async def audio_server_start(req: AudioServerStartRequest) -> dict[str, Any]:
    return audio_server_control.start(req.host, req.port, req.uiHost)


@app.post("/api/audio-server/stop")
async def audio_server_stop() -> dict[str, Any]:
    return audio_server_control.stop()


@app.get("/api/audio-server/status")
async def audio_server_status() -> dict[str, Any]:
    return audio_server_control.status()


@app.post("/api/audio-reverse/connect")
async def audio_reverse_connect(req: AudioReverseConnectRequest) -> dict[str, Any]:
    try:
        bridge = await _connect_reverse_audio(
            _settings_for(req.agentId, req.settings), req.providerId
        )
        return {"ok": True, **bridge}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/audio-reverse/status")
async def audio_reverse_status() -> dict[str, Any]:
    if _reverse_audio is None:
        return {"connected": False, "target": "", "lastError": "reverse audio is disabled"}
    return _reverse_audio.status()


async def _connect_reverse_audio(settings: ClientSettings, provider_id: str) -> dict[str, Any]:
    """Discover and connect one selected reverse-audio provider via Atlas."""
    global _reverse_audio
    bridge = await discover_audio_bridge(settings, provider_id)
    if _reverse_audio is None:
        _reverse_audio = AudioReverseBridge(
            bridge["endpoint"], audio_server_control.DEFAULT_BRIDGE_PORT
        )
        _reverse_audio.start()
    else:
        _reverse_audio.set_target(bridge["endpoint"])
    for _ in range(20):
        status = _reverse_audio.status()
        if status.get("connected"):
            return {**bridge, **status}
        await asyncio.sleep(0.1)
    status = _reverse_audio.status()
    raise RuntimeError(
        f"audio bridge did not connect to {bridge['endpoint']}: "
        f"{status.get('lastError') or 'timeout'}"
    )


async def _connect_selected_reverse_audio(settings: ClientSettings) -> dict[str, Any] | None:
    """Connect only when the current audio route selects an Atlas bridge.

    Device selection remains provider-agnostic: a robot USB driver needs no
    client-side connection, while a reverse bridge is discovered from its
    declared capability rather than a provider name or fixed port.
    """
    selected = [
        provider_id
        for provider_id in (settings.mic_node_id, settings.speaker_node_id)
        if provider_id
    ]
    if not selected:
        return None
    providers = await list_audio_providers(settings)
    bridge_ids = {
        str(provider.get("id") or "")
        for provider in providers.get("bridgeProviders", [])
        if isinstance(provider, dict)
    }
    for provider_id in selected:
        if provider_id in bridge_ids:
            return await _connect_reverse_audio(settings, provider_id)
    return None


@app.get("/api/audio-server/health")
async def audio_server_health(
    host: str = Query(audio_server_control.DEFAULT_BRIDGE_HOST),
    port: int = Query(audio_server_control.DEFAULT_BRIDGE_PORT),
) -> dict[str, Any]:
    return await audio_server_control.health(host, port)


@app.post("/api/voiceprint/enroll")
async def voiceprint_enroll(req: EnrollRequest) -> dict[str, Any]:
    try:
        settings = _settings_for(req.agentId, req.settings)
        return await enroll_voiceprint(settings, req.userId, req.userName, req.seconds)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/audio/play-test")
async def audio_play_test(req: AudioPlayTestRequest) -> dict[str, Any]:
    try:
        settings = _settings_for(req.agentId, req.settings)
        return await play_tts_test(settings, req.text)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/audio/mic-test")
async def audio_mic_test(req: AudioMicTestRequest) -> dict[str, Any]:
    """Verify the selected Robonix microphone path with a short PCM capture."""
    try:
        settings = _settings_for(req.agentId, req.settings)
        handsfree = await get_handsfree_status(settings)
        if (
            handsfree.get("enabled")
            and handsfree.get("micProviderId") == settings.mic_node_id
        ):
            return {
                "ok": False,
                "error": "Hands-free is listening on this microphone. Turn it off before running an exclusive microphone test.",
            }
        await _connect_selected_reverse_audio(settings)
        seconds = min(3.0, max(0.5, float(req.seconds)))
        started = time.monotonic()
        pcm = await record_pcm(settings, seconds)
        capture_ms = round((time.monotonic() - started) * 1000.0)
        stats = pcm16_stats(pcm)
        return {
            "ok": True,
            "bytes": len(pcm),
            "seconds": seconds,
            "rms": round(float(stats["rms"]), 4),
            "peak": stats["peak"],
            "nonzeroRatio": round(float(stats["nonzeroRatio"]), 6),
            "captureMs": capture_ms,
            "providerId": settings.mic_node_id or "auto",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.websocket("/ws/task")
async def task_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        payload = await ws.receive_json()
        settings = _settings_for(payload.get("agentId"), payload.get("settings"))
        text = (payload.get("text") or "").strip()
        attachments = payload.get("attachments") or []
        steer = _payload_steer(payload)
        expected_turn_id = _payload_expected_turn_id(payload)
        if not text and not attachments:
            await ws.send_json({"type": "error", "error": "empty task"})
            return
        await ws.send_json({"type": "accepted", "sessionId": settings.session_id})
        async for item in submit_text(
            settings,
            text,
            attachments,
            steer=steer,
            expected_turn_id=expected_turn_id,
        ):
            await ws.send_json(item)
        await ws.send_json({"type": "done"})
    except WebSocketDisconnect:
        return
    except grpc.aio.AioRpcError as exc:
        await _send_error(ws, f"gRPC {exc.code().name}: {exc.details()}")
    except Exception as exc:
        await _send_error(ws, str(exc))


@app.websocket("/ws/abort")
async def abort_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        payload = await ws.receive_json()
        settings = _settings_for(payload.get("agentId"), payload.get("settings"))
        expected_turn_id = _payload_expected_turn_id(payload)
        await ws.send_json({"type": "accepted", "sessionId": settings.session_id})
        async for item in abort_turn(settings, expected_turn_id):
            await ws.send_json(item)
        await ws.send_json({"type": "done"})
    except WebSocketDisconnect:
        return
    except grpc.aio.AioRpcError as exc:
        await _send_error(ws, f"gRPC {exc.code().name}: {exc.details()}")
    except Exception as exc:
        await _send_error(ws, str(exc))


@app.websocket("/ws/voice")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        payload = await ws.receive_json()
        settings = _settings_for(payload.get("agentId"), payload.get("settings"))
        steer = _payload_steer(payload)
        expected_turn_id = _payload_expected_turn_id(payload)
        await _connect_selected_reverse_audio(settings)
        await ws.send_json({"type": "accepted", "sessionId": settings.session_id})

        async def relay_voice() -> None:
            async for item in start_voice_session(
                settings,
                steer=steer,
                expected_turn_id=expected_turn_id,
            ):
                await ws.send_json(item)

        async def wait_for_stop() -> None:
            while True:
                control = await ws.receive_json()
                control_type = control.get("type")
                if control_type == "stop":
                    return
                if control_type == "finish":
                    # Ask Liaison to stop capturing and submit whatever it has
                    # already recognized, instead of discarding the turn. The
                    # relay task keeps running -- Liaison flushes a normal
                    # asr_final/session_done sequence down the same stream.
                    try:
                        result = await finish_voice_capture(settings)
                        await ws.send_json({"type": "finish_requested", **result})
                    except grpc.aio.AioRpcError as exc:
                        await ws.send_json(
                            {
                                "type": "finish_requested",
                                "ok": False,
                                "detail": f"gRPC {exc.code().name}: {exc.details()}",
                            }
                        )
                    except Exception as exc:
                        await ws.send_json({"type": "finish_requested", "ok": False, "detail": str(exc)})

        relay_task = asyncio.create_task(relay_voice())
        stop_task = asyncio.create_task(wait_for_stop())
        done, _ = await asyncio.wait(
            {relay_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            relay_task.cancel()
            with suppress(asyncio.CancelledError):
                await relay_task
            await ws.send_json({"type": "status", "message": "voice session stopped"})
        else:
            stop_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_task
            await relay_task
        await ws.send_json({"type": "done"})
    except WebSocketDisconnect:
        return
    except grpc.aio.AioRpcError as exc:
        await _send_error(ws, f"gRPC {exc.code().name}: {exc.details()}")
    except Exception as exc:
        await _send_error(ws, str(exc))


@app.websocket("/ws/handsfree-events")
async def handsfree_events_ws(ws: WebSocket) -> None:
    """Relay Liaison's persistent robot-local hands-free VoiceEvent stream."""
    await ws.accept()
    try:
        payload = await ws.receive_json()
        settings = _settings_for(payload.get("agentId"), payload.get("settings"))
        await _connect_selected_reverse_audio(settings)
        await ws.send_json({"type": "accepted", "sessionId": settings.session_id})
        async for item in watch_handsfree_events(settings):
            await ws.send_json(item)
    except WebSocketDisconnect:
        return
    except grpc.aio.AioRpcError as exc:
        await _send_error(ws, f"gRPC {exc.code().name}: {exc.details()}")
    except Exception as exc:
        await _send_error(ws, str(exc))


async def _send_error(ws: WebSocket, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "error": message})
    except (RuntimeError, WebSocketDisconnect):
        pass
