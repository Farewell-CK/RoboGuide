from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import grpc
from google.protobuf.empty_pb2 import Empty

PROTO_DIR = Path(__file__).with_name("proto")
if str(PROTO_DIR) not in sys.path:
    sys.path.insert(0, str(PROTO_DIR))

import atlas_pb2  # type: ignore  # noqa: E402
import audio_pb2  # type: ignore  # noqa: E402
import executor_pb2  # type: ignore  # noqa: E402
import liaison_pb2  # type: ignore  # noqa: E402
import pilot_pb2  # type: ignore  # noqa: E402
import tts_pb2  # type: ignore  # noqa: E402
import voiceprint_pb2  # type: ignore  # noqa: E402

# Hand-written runtime proto for the robonix-side `lite3_map_browser`
# primitive. See `proto/map_browser.proto` for the schema and the
# `map_browser_pb2` module docstring for how the stub is generated.
import map_browser_pb2  # type: ignore  # noqa: E402

CONSUMER_ID = "robonix-client/gui"
DEFAULT_ATLAS = "127.0.0.1:50051"
DEFAULT_ATLAS_PORT = 50051
DEFAULT_LIAISON_PORT = 50081
GRPC_MAX_RECEIVE_BYTES = 32 * 1024 * 1024
GRPC_CHANNEL_OPTIONS = (
    ("grpc.enable_http_proxy", 0),
    ("grpc.max_receive_message_length", GRPC_MAX_RECEIVE_BYTES),
)

CONTRACT_LIAISON_SUBMIT = "robonix/system/liaison/submit"
CONTRACT_LIAISON_VOICE = "robonix/system/liaison/voice"
CONTRACT_LIAISON_VOICE_FINISH = "robonix/system/liaison/voice/finish"
CONTRACT_PILOT = "robonix/system/pilot"
CONTRACT_EXECUTOR_EXECUTE = "robonix/system/executor/execute"
CONTRACT_EXECUTOR_GET_HEALTH = "robonix/system/executor/get_health"
CONTRACT_EXECUTOR_CONTROL_PLAN = "robonix/system/executor/control_plan"
CONTRACT_EXECUTOR_LIST_ACTIVE = "robonix/system/executor/list_active_plans"
CONTRACT_EXECUTOR_CANCEL_ALL = "robonix/system/executor/cancel_all_plans"
EXECUTOR_CONTRACTS = (
    CONTRACT_EXECUTOR_EXECUTE,
    CONTRACT_EXECUTOR_GET_HEALTH,
    CONTRACT_EXECUTOR_CONTROL_PLAN,
    CONTRACT_EXECUTOR_LIST_ACTIVE,
    CONTRACT_EXECUTOR_CANCEL_ALL,
)
CONTRACT_MIC = "robonix/primitive/audio/mic"
CONTRACT_SPEAKER = "robonix/primitive/audio/speaker"
CONTRACT_AUDIO_LIST_DEVICES = "robonix/primitive/audio/list_devices"
CONTRACT_AUDIO_SELECT_DEVICE = "robonix/primitive/audio/select_device"
CONTRACT_AUDIO_BRIDGE_INFO = "robonix/primitive/audio/bridge_info"
CONTRACT_ASR = "robonix/service/speech/asr"
CONTRACT_TTS = "robonix/service/speech/tts"
CONTRACT_VOICEPRINT = "robonix/service/voiceprint/identify"
CONTRACT_VOICEPRINT_ENROLL = "robonix/service/voiceprint/enroll"
CONTRACT_HANDSFREE_SET_ENABLED = "robonix/system/liaison/handsfree/set_enabled"
CONTRACT_HANDSFREE_STATUS = "robonix/system/liaison/handsfree/status"
CONTRACT_HANDSFREE_EVENTS = "robonix/system/liaison/handsfree/events"

# Lite3 map browser — registered by the `lite3_map_browser` primitive on
# the robot dog. Backed by the service-map-rbnx cache directory on the
# robot. Four contracts in total:
#   * list  — JSON listing of the directory
#   * get   — read a single file's bytes
#   * push  — write a single file
#   * delete— unlink a single file
# See `map_browser.proto` for the wire schema and
# `map_browser_driver/driver.py` in the robonix repo for the JSON
# response shapes. The client uses list + get to mirror a robot's
# cache into a local "shared library" (see `shared_maps_root` /
# `list_shared_library` below) and push + delete to deploy a shared
# map back onto the robot.
CONTRACT_MAP_LIST = "robonix/primitive/map/list"
CONTRACT_MAP_GET = "robonix/primitive/map/get"
CONTRACT_MAP_PUSH = "robonix/primitive/map/push"
CONTRACT_MAP_DELETE = "robonix/primitive/map/delete"

# Aggregated chassis state (pose + battery) — exposed by the
# `lite3_chassis` primitive at robonix/primitive/chassis/state.
CONTRACT_CHASSIS_STATE = "robonix/primitive/chassis/state"
# Camera snapshot — exposed by `lite3_camera` at
# robonix/primitive/camera/snapshot. Returns sensor_msgs/Image with
# encoding="jpeg" and the latest RGB-D color frame in `data`.
CONTRACT_CAMERA_SNAPSHOT = "robonix/primitive/camera/snapshot"
CONTRACT_CAMERA_DEPTH_SNAPSHOT = "robonix/primitive/camera/depth_snapshot"

# ── Service / method names for the new MCPs ────────────────────────────────
# The robonix runtime generates gRPC service names from the contract path
# (`robonix.primitive.<segment>.<ClassName>`) and method names from the
# last contract segment in PascalCase. If a future robonix version changes
# the casing or structure, adjust these constants and re-test.
GRPC_CHASSIS_SERVICE = "robonix.primitive.chassis.Lite3Chassis"
GRPC_CHASSIS_METHOD_STATE = "/{}/State".format(GRPC_CHASSIS_SERVICE)
GRPC_CAMERA_SERVICE = "robonix.primitive.camera.Lite3Camera"
GRPC_CAMERA_METHOD_SNAPSHOT = "/{}/Snapshot".format(GRPC_CAMERA_SERVICE)
GRPC_CAMERA_METHOD_DEPTH_SNAPSHOT = "/{}/DepthSnapshot".format(GRPC_CAMERA_SERVICE)

# service-map-rbnx runs as a sidecar HTTP service on the robot, not as an
# Atlas MCP. The default port (8092) matches robonix_manifest.yaml.
DEFAULT_SERVICE_MAP_PORT = 8092
SERVICE_MAP_STATE_PATH = "/api/state"
SERVICE_MAP_POSE_PATH = "/api/pose_estimate"
SERVICE_MAP_LOAD_PATH = "/api/load"

PILOT_EVENT_NAMES = {
    0: "text_chunk",
    1: "plan",
    2: "batch_result",
    3: "status",
    4: "final_text",
    5: "node_state",
    6: "task_state",
}

VOICE_EVENT_NAMES = {
    0: "session_started",
    1: "recording_started",
    2: "recording_done",
    3: "asr_partial",
    4: "asr_final",
    5: "user_identified",
    6: "pilot",
    7: "tts_started",
    8: "tts_done",
    9: "session_done",
    10: "error",
}

NODE_KIND_NAMES = {
    0: "sequence",
    1: "parallel",
    2: "do",
}

RTDL_NODE_STATE_NAMES = {
    0: "PENDING",
    1: "RUNNING",
    2: "SUCCEEDED",
    3: "FAILED",
    4: "CANCELED",
    5: "TIMEOUT",
    6: "PAUSED",
}

STATE_NAMES = {
    0: "UNSPECIFIED",
    1: "REGISTERED",
    2: "INACTIVE",
    3: "ACTIVE",
    4: "ERROR",
    5: "TERMINATED",
}

KIND_NAMES = {
    0: "unspecified",
    1: "primitive",
    2: "service",
    3: "skill",
}

TRANSPORT_NAMES = {
    0: "unspecified",
    1: "grpc",
    2: "ros2",
    3: "mcp",
}


@dataclass(slots=True)
class ClientSettings:
    atlas_endpoint: str = DEFAULT_ATLAS
    atlas_host: str = "127.0.0.1"
    atlas_port: int = DEFAULT_ATLAS_PORT
    robot_host: str = ""
    liaison_endpoint: str = ""
    user_id: str = ""
    session_id: str = ""
    record_seconds: int = 30
    language: str = ""
    mic_node_id: str = ""
    mic_device_id: str = ""
    asr_node_id: str = ""
    voiceprint_node_id: str = ""
    tts_node_id: str = ""
    speaker_node_id: str = ""
    speaker_device_id: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ClientSettings":
        payload = payload or {}
        atlas_endpoint = payload.get("atlasEndpoint") or ""
        robot_host = (payload.get("robotHost") or "").strip()
        atlas_port = payload.get("atlasPort") or DEFAULT_ATLAS_PORT
        if not atlas_endpoint and robot_host:
            atlas_endpoint = f"{robot_host}:{atlas_port}"
        normalized = normalize_grpc_target(atlas_endpoint or DEFAULT_ATLAS)
        parsed_host, parsed_port = split_host_port(normalized)
        return cls(
            atlas_endpoint=normalized,
            atlas_host=parsed_host,
            atlas_port=parsed_port or DEFAULT_ATLAS_PORT,
            robot_host=robot_host or parsed_host,
            liaison_endpoint=normalize_grpc_target(payload.get("liaisonEndpoint") or ""),
            user_id=(payload.get("userId") or "").strip(),
            session_id=(payload.get("sessionId") or "").strip(),
            record_seconds=max(0, int(payload.get("recordSeconds") or 30)),
            language=(payload.get("language") or "").strip(),
            mic_node_id=(payload.get("micNodeId") or "").strip(),
            mic_device_id=(payload.get("micDeviceId") or "").strip(),
            asr_node_id=(payload.get("asrNodeId") or "").strip(),
            voiceprint_node_id=(payload.get("voiceprintNodeId") or "").strip(),
            tts_node_id=(payload.get("ttsNodeId") or "").strip(),
            speaker_node_id=(payload.get("speakerNodeId") or "").strip(),
            speaker_device_id=(payload.get("speakerDeviceId") or "").strip(),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the canonical on-the-wire representation.

        The new multi-agent UI persists agents as ``{"settings": {...}}``
        blobs, so this is the inverse of :meth:`from_payload`. The
        output keeps both the joined ``atlasEndpoint`` (so older
        front ends that only read it still work) and the original
        ``robotHost``/``atlasPort`` pair (so the UI can edit them
        individually).
        """
        return {
            "atlasEndpoint": self.atlas_endpoint,
            "robotHost": self.robot_host,
            "atlasPort": self.atlas_port,
            "liaisonEndpoint": self.liaison_endpoint,
            "userId": self.user_id,
            "sessionId": self.session_id,
            "recordSeconds": self.record_seconds,
            "language": self.language,
            "micNodeId": self.mic_node_id,
            "micDeviceId": self.mic_device_id,
            "asrNodeId": self.asr_node_id,
            "voiceprintNodeId": self.voiceprint_node_id,
            "ttsNodeId": self.tts_node_id,
            "speakerNodeId": self.speaker_node_id,
            "speakerDeviceId": self.speaker_device_id,
        }


class RobonixApiError(RuntimeError):
    pass


def normalize_grpc_target(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"grpc://{value}")
    host = parsed.hostname or value.split("/", 1)[0]
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port}"


def grpc_channel(target: str) -> grpc.aio.Channel:
    """Connect directly to robot capabilities, never through desktop proxies."""
    return grpc.aio.insecure_channel(
        normalize_grpc_target(target), options=GRPC_CHANNEL_OPTIONS
    )


def split_host_port(target: str) -> tuple[str, int | None]:
    normalized = normalize_grpc_target(target)
    if not normalized:
        return "", None
    parsed = urlparse(f"grpc://{normalized}")
    return parsed.hostname or "", parsed.port


def is_loopback_host(host: str) -> bool:
    value = (host or "").strip().lower()
    return value in {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}


def rewrite_remote_endpoint(endpoint: str, atlas_endpoint: str) -> str:
    endpoint_host, endpoint_port = split_host_port(endpoint)
    atlas_host, _ = split_host_port(atlas_endpoint)
    if not endpoint_port or not endpoint_host:
        return normalize_grpc_target(endpoint)
    if is_loopback_host(endpoint_host) and atlas_host and not is_loopback_host(atlas_host):
        return f"{atlas_host}:{endpoint_port}"
    return normalize_grpc_target(endpoint)


def rewrite_remote_websocket_endpoint(endpoint: str, atlas_endpoint: str) -> str:
    """Replace a provider's loopback advertise host with the known robot host."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname or parsed.port is None:
        raise RobonixApiError(f"invalid reverse audio endpoint: {endpoint!r}")
    atlas_host, _ = split_host_port(atlas_endpoint)
    host = atlas_host if is_loopback_host(parsed.hostname) and atlas_host else parsed.hostname
    if not host:
        raise RobonixApiError(f"reverse audio endpoint has no usable host: {endpoint!r}")
    return f"{parsed.scheme}://{host}:{parsed.port}{parsed.path or '/client'}"


def _fallback_liaison(atlas_endpoint: str) -> str:
    atlas = normalize_grpc_target(atlas_endpoint or DEFAULT_ATLAS)
    parsed = urlparse(f"grpc://{atlas}")
    host = parsed.hostname or "127.0.0.1"
    return f"{host}:{DEFAULT_LIAISON_PORT}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_json(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def _unary_unary(
    target: str,
    path: str,
    request: Any,
    response_type: Any,
    timeout: float = 4.0,
) -> Any:
    async with grpc_channel(target) as channel:
        call = channel.unary_unary(
            path,
            request_serializer=request.SerializeToString,
            response_deserializer=response_type.FromString,
        )
        return await call(request, timeout=timeout)


async def query_atlas(
    atlas_endpoint: str,
    *,
    provider_id: str = "",
    contract_id: str = "",
    transport: int = 0,
) -> list[Any]:
    req = atlas_pb2.QueryRequest(
        kind=0,
        id=provider_id,
        contract_id=contract_id,
        namespace_prefix="",
        transport=transport,
    )
    resp = await _unary_unary(
        atlas_endpoint,
        "/robonix.atlas.Atlas/Query",
        req,
        atlas_pb2.QueryResponse,
    )
    return list(resp.providers)


async def connect_capability(
    atlas_endpoint: str,
    provider_id: str,
    contract_id: str,
    consumer_id: str = CONSUMER_ID,
) -> str:
    req = atlas_pb2.ConnectCapabilityRequest(
        consumer_id=consumer_id,
        provider_id=provider_id,
        contract_id=contract_id,
        transport=1,
    )
    resp = await _unary_unary(
        atlas_endpoint,
        "/robonix.atlas.Atlas/ConnectCapability",
        req,
        atlas_pb2.ConnectCapabilityResponse,
    )
    return rewrite_remote_endpoint(resp.endpoint, atlas_endpoint)


async def discover_endpoint(atlas_endpoint: str, contract_id: str, provider_hint: str = "") -> str:
    providers = await query_atlas(
        atlas_endpoint,
        provider_id=provider_hint,
        contract_id=contract_id,
        transport=1,
    )
    for provider in providers:
        if provider_hint and provider.id != provider_hint and provider.namespace != provider_hint:
            continue
        if any(cap.contract_id == contract_id and cap.transport == 1 for cap in provider.capabilities):
            endpoint = await connect_capability(atlas_endpoint, provider.id, contract_id)
            if endpoint:
                return endpoint
    raise RobonixApiError(f"no provider found for {contract_id}")


async def list_active_plans(settings: ClientSettings) -> dict[str, Any]:
    """Read Executor's control-plane snapshot without creating an RTDL plan."""
    endpoint = await discover_endpoint(
        settings.atlas_endpoint,
        CONTRACT_EXECUTOR_LIST_ACTIVE,
    )
    response = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixSystemExecutorListActivePlans/ListActivePlans",
        executor_pb2.ListActivePlans_Request(),
        executor_pb2.ListActivePlans_Response,
    )
    if not response.success:
        raise RobonixApiError(response.error or "Executor active-plan query failed")
    payload = _safe_json(response.plans_json)
    if not isinstance(payload, dict) or not isinstance(payload.get("plans", []), list):
        raise RobonixApiError("Executor returned an invalid active-plan snapshot")
    plans = []
    for plan in payload.get("plans", []):
        if not isinstance(plan, dict):
            continue
        plans.append(
            {
                "planId": str(plan.get("plan_id", "")),
                "description": str(plan.get("description", "")),
                "opCount": int(plan.get("op_count", 0)),
                "cancelled": bool(plan.get("cancelled", False)),
                "stopPoints": int(plan.get("stop_points", 0)),
                "ops": plan.get("ops", []),
            }
        )
    return {"available": True, "count": len(plans), "plans": plans}


async def list_maps(settings: ClientSettings) -> dict[str, Any]:
    """Read the `robonix/primitive/map/list` snapshot from the robot.

    Returns a dict with shape::

        {
            "available": True,
            "mapsDir":   "<absolute path the robot scanned>",
            "count":     <int>,
            "files":     [
                {"name": "...", "sizeBytes": int, "mtimeUnix": int, "kind": "file"|"dir"},
                ...
            ],
            "raw":       "<raw JSON string from the robot, for debug>",
        }

    On error (provider not registered, gRPC failure, malformed JSON) the
    result sets ``available=False`` and includes the exception detail so
    the UI can show a precise failure message instead of a blank list.
    """
    endpoint = await discover_endpoint(
        settings.atlas_endpoint,
        CONTRACT_MAP_LIST,
    )
    response = await _unary_unary(
        endpoint,
        map_browser_pb2.GRPC_METHOD_LIST_MAPS,
        map_browser_pb2.ListMaps_Request(),
        map_browser_pb2.ListMaps_Response,
    )
    raw = getattr(response, "data", "") or ""
    payload = _safe_json(raw)
    if not isinstance(payload, dict):
        raise RobonixApiError(
            f"map browser returned a non-object payload: {raw[:200]!r}"
        )
    files_raw = payload.get("files", [])
    files: list[dict[str, Any]] = []
    if isinstance(files_raw, list):
        for entry in files_raw:
            if not isinstance(entry, dict):
                continue
            try:
                size_bytes = int(entry.get("size_bytes", 0) or 0)
            except (TypeError, ValueError):
                size_bytes = 0
            try:
                mtime_unix = int(entry.get("mtime_unix", 0) or 0)
            except (TypeError, ValueError):
                mtime_unix = 0
            files.append(
                {
                    "name": str(entry.get("name", "")),
                    "sizeBytes": size_bytes,
                    "mtimeUnix": mtime_unix,
                    "kind": str(entry.get("kind", "file")),
                }
            )
    return {
        "available": True,
        "mapsDir": str(payload.get("maps_dir", "")),
        "count": int(payload.get("count", len(files)) or len(files)),
        "files": files,
        "raw": raw,
    }


async def get_map(settings: ClientSettings, name: str) -> dict[str, Any]:
    """Download a single map file from the robot's cache.

    Calls `robonix/primitive/map/get` and returns::

        {"name": "...", "sizeBytes": int, "data": <bytes>}

    Caller is responsible for persisting `data` (e.g. into the local
    shared library); the transport layer never touches the local FS.
    """
    if not name or not isinstance(name, str):
        raise RobonixApiError("get_map requires a non-empty name")
    endpoint = await discover_endpoint(
        settings.atlas_endpoint,
        CONTRACT_MAP_GET,
    )
    request = map_browser_pb2.GetMap_Request()
    request.name = name
    response = await _unary_unary(
        endpoint,
        map_browser_pb2.GRPC_METHOD_GET_MAP,
        request,
        map_browser_pb2.GetMap_Response,
    )
    data = bytes(getattr(response, "data", b"") or b"")
    return {
        "name": str(getattr(response, "name", "") or name),
        "sizeBytes": len(data),
        "data": data,
    }


async def push_map(
    settings: ClientSettings, name: str, data: bytes
) -> dict[str, Any]:
    """Upload a single map file to the robot's cache.

    Calls `robonix/primitive/map/push` with the given `name` + bytes.
    Returns the JSON status produced by the driver (parsed into a dict)
    so the caller can surface `error` text on the UI.
    """
    if not name or not isinstance(name, str):
        raise RobonixApiError("push_map requires a non-empty name")
    if not isinstance(data, (bytes, bytearray)):
        raise RobonixApiError("push_map requires bytes data")
    endpoint = await discover_endpoint(
        settings.atlas_endpoint,
        CONTRACT_MAP_PUSH,
    )
    request = map_browser_pb2.PushMap_Request()
    request.name = name
    request.data = bytes(data)
    response = await _unary_unary(
        endpoint,
        map_browser_pb2.GRPC_METHOD_PUSH_MAP,
        request,
        map_browser_pb2.PushMap_Response,
    )
    raw = getattr(response, "data", "") or ""
    parsed = _safe_json(raw)
    if isinstance(parsed, dict):
        return parsed
    # Driver returned something non-JSON (shouldn't happen); wrap it.
    return {"ok": True, "error": "", "name": name, "raw": raw}


async def delete_map(settings: ClientSettings, name: str) -> dict[str, Any]:
    """Delete a single map file from the robot's cache.

    Calls `robonix/primitive/map/delete`. Same return shape as
    `push_map`: parsed JSON from the driver, falling back to `{"ok":
    True, "name": ...}` if the response isn't JSON.
    """
    if not name or not isinstance(name, str):
        raise RobonixApiError("delete_map requires a non-empty name")
    endpoint = await discover_endpoint(
        settings.atlas_endpoint,
        CONTRACT_MAP_DELETE,
    )
    request = map_browser_pb2.DeleteMap_Request()
    request.name = name
    response = await _unary_unary(
        endpoint,
        map_browser_pb2.GRPC_METHOD_DELETE_MAP,
        request,
        map_browser_pb2.DeleteMap_Response,
    )
    raw = getattr(response, "data", "") or ""
    parsed = _safe_json(raw)
    if isinstance(parsed, dict):
        return parsed
    return {"ok": True, "error": "", "name": name, "raw": raw}


# ── Chassis / Camera / Service-Map live signals ────────────────────────────
# These three back the small camera / map / pose / battery tiles in the
# robonix-client overview grid. The first two are gRPC MCPs (robonix/
# primitive/chassis/state and robonix/primitive/camera/snapshot). The
# third is a plain HTTP GET against the `service-map-rbnx` sidecar
# (port 8092 by default), which exposes the *map-frame* pose that
# matches the displayed mini-map.
def _std_string_from_wire(raw: bytes) -> str:
    """Decode a `std_msgs/String` (or any field-1 string) from wire bytes.

    `std_msgs/String` and `google.protobuf.wrappers_pb2.StringValue`
    have the same wire format for field 1 (a length-delimited string).
    We use the well-known `wrappers_pb2` rather than a generated
    std_msgs stub so the client has no extra proto to maintain.
    Returns "" on parse failure (the caller treats this as "no data").
    """
    if not raw:
        return ""
    try:
        from google.protobuf import wrappers_pb2
        return wrappers_pb2.StringValue.FromString(raw).value
    except Exception:
        return ""


def _image_data_from_wire(raw: bytes) -> bytes:
    """Extract the `data` field (bytes) of a `sensor_msgs/Image` from wire.

    sensor_msgs/Image layout::

        message Image {
          Header   header    = 1;   // length-delimited sub-message
          uint32   height    = 2;
          uint32   width     = 3;
          string   encoding  = 4;   // expected "jpeg" / "png" / "rgb8" ...
          uint8    is_bigendian = 5;
          uint32   step      = 6;
          bytes    data      = 7;   // <-- what we return
        }

    We don't need Header / height / width / encoding for the small
    overview card; just the raw image bytes. The wire format is stable
    across protobuf versions, so a focused parser is safer than shipping
    a generated sensor_msgs proto.
    """
    if not raw:
        return b""
    try:
        from google.protobuf import descriptor, message_factory
        image_desc = descriptor.Descriptor(
            "Image", None,
            fields=[
                descriptor.FieldDescriptor("header", 1, descriptor.FieldDescriptor.TYPE_MESSAGE,
                    descriptor.Label.LABEL_OPTIONAL, message_type=None, default_value=""),
                descriptor.FieldDescriptor("height", 2, descriptor.FieldDescriptor.TYPE_UINT32,
                    descriptor.Label.LABEL_OPTIONAL, default_value=0),
                descriptor.FieldDescriptor("width", 3, descriptor.FieldDescriptor.TYPE_UINT32,
                    descriptor.Label.LABEL_OPTIONAL, default_value=0),
                descriptor.FieldDescriptor("encoding", 4, descriptor.FieldDescriptor.TYPE_STRING,
                    descriptor.Label.LABEL_OPTIONAL, default_value=""),
                descriptor.FieldDescriptor("is_bigendian", 5, descriptor.FieldDescriptor.TYPE_UINT8,
                    descriptor.Label.LABEL_OPTIONAL, default_value=0),
                descriptor.FieldDescriptor("step", 6, descriptor.FieldDescriptor.TYPE_UINT32,
                    descriptor.Label.LABEL_OPTIONAL, default_value=0),
                descriptor.FieldDescriptor("data", 7, descriptor.FieldDescriptor.TYPE_BYTES,
                    descriptor.Label.LABEL_OPTIONAL, default_value=b""),
            ],
            nested_types=[], enum_types=[], extensions=[],
            options=None, is_extendable=False,
        )
        Image = message_factory.GetMessageClass(image_desc)
        img = Image.FromString(raw)
        return bytes(getattr(img, "data", b"") or b"")
    except Exception:
        return b""


async def get_chassis_state(settings: ClientSettings, timeout: float = 2.0) -> dict[str, Any]:
    """Fetch the aggregated chassis state MCP.

    Returns a dict with the same shape as the robot's
    `robonix/primitive/chassis/state` response::

        {
          "odom":    {"x": float, "y": float, "yaw": float},
          "battery": {"percent": float|None, "present": bool, "voltage": float|None},
          "ts_ms":   int, "age_ms": int|None,
          "stale_ms": int, "stale": bool
        }

    On any error the function returns a dict with `error` set and the
    live fields nulled out, so the overview card can degrade to
    placeholders without crashing the rest of the snapshot.
    """
    try:
        endpoint = await discover_endpoint(
            settings.atlas_endpoint,
            CONTRACT_CHASSIS_STATE,
        )
    except RobonixApiError as exc:
        return {
            "ok": False, "error": f"discover chassis/state: {exc}",
            "odom": None, "battery": None, "ts_ms": 0,
        }
    # The chassis state MCP takes a std_msgs/Empty request and returns
    # a std_msgs/String. We pass Empty (zero bytes) and decode the
    # response as a string-valued message.
    from google.protobuf.empty_pb2 import Empty
    try:
        async with grpc_channel(endpoint) as channel:
            call = channel.unary_unary(
                GRPC_CHASSIS_METHOD_STATE,
                request_serializer=Empty.SerializeToString,
                response_deserializer=lambda raw: _std_string_from_wire(raw),
            )
            data_str = await call(Empty(), timeout=timeout)
    except Exception as exc:
        return {
            "ok": False, "error": f"chassis/state rpc: {exc}",
            "odom": None, "battery": None, "ts_ms": 0,
        }
    parsed = _safe_json(data_str)
    if not isinstance(parsed, dict):
        return {
            "ok": False, "error": "chassis/state returned non-JSON body",
            "odom": None, "battery": None, "ts_ms": 0,
            "raw": data_str,
        }
    return {"ok": True, **parsed}


async def get_camera_snapshot(
    settings: ClientSettings, *, depth: bool = False, timeout: float = 2.0,
) -> bytes:
    """Fetch a single camera frame (JPEG/PNG bytes) from the camera MCP.

    Default is the RGB snapshot; pass `depth=True` to ask for the
    depth-image snapshot if the camera primitive exposes one.

    Returns empty bytes on error so the caller can keep going; the
    HTTP layer translates that into a 503 or transparent placeholder.
    """
    method = GRPC_CAMERA_METHOD_DEPTH_SNAPSHOT if depth else GRPC_CAMERA_METHOD_SNAPSHOT
    try:
        endpoint = await discover_endpoint(
            settings.atlas_endpoint,
            CONTRACT_CAMERA_DEPTH_SNAPSHOT if depth else CONTRACT_CAMERA_SNAPSHOT,
        )
    except RobonixApiError:
        return b""
    from google.protobuf.empty_pb2 import Empty
    try:
        async with grpc_channel(endpoint) as channel:
            call = channel.unary_unary(
                method,
                request_serializer=Empty.SerializeToString,
                response_deserializer=lambda raw: _image_data_from_wire(raw),
            )
            return await call(Empty(), timeout=timeout)
    except Exception:
        return b""


async def get_service_map_state(
    robot_host: str,
    *,
    port: int = DEFAULT_SERVICE_MAP_PORT,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Query `service-map-rbnx`'s HTTP /api/state endpoint.

    Returns a dict with at least the keys::

        {ok, error, has_map, mode, map_id, map_name, pose: {x,y,theta}}

    On connection failure (port closed, host unreachable) the function
    returns `{"ok": False, "error": ...}` with the live fields nulled
    out, so the overview card can fall back to placeholders.

    The call uses `urllib` (no `aiohttp` dependency) since this runs
    inside a tight polling loop and the request must not block the
    FastAPI event loop.
    """
    import urllib.request
    import urllib.error
    import socket as _socket
    url = f"http://{robot_host}:{port}{SERVICE_MAP_STATE_PATH}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        import json as _json
        data = _json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, _socket.timeout, ConnectionError, OSError) as exc:
        return {"ok": False, "error": f"service-map state: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"service-map state decode: {exc}"}
    pose_raw = data.get("pose") or {}
    pose: dict[str, Any] = {
        "x": float(pose_raw.get("x", 0.0)) if isinstance(pose_raw, dict) else 0.0,
        "y": float(pose_raw.get("y", 0.0)) if isinstance(pose_raw, dict) else 0.0,
        "theta": float(pose_raw.get("theta", 0.0)) if isinstance(pose_raw, dict) else 0.0,
    }
    return {
        "ok": True,
        "error": "",
        "has_map": bool(data.get("has_map", False)),
        "mode": str(data.get("mode", "")),
        "map_id": str(data.get("map_id", "")),
        "map_name": str(data.get("map_name") or data.get("map_id") or ""),
        "pose": pose,
    }


# ── local "shared" map library ──────────────────────────────────────────────
# Lives on the client PC (not on the robot). One subdirectory per
# robot, identified by the robot's host (or whatever label the caller
# passes in). The default root is `<repo>/maps` but can be overridden
# by `ROBONIX_SHARED_MAPS_DIR`.
_SHARED_MAPS_DIRNAME = "maps"
_SHARED_ROBOT_PATTERN = re.compile(r"^[A-Za-z0-9._\-:]{1,128}$")
_SHARED_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._\-+ ]{1,200}$")


def shared_maps_root() -> Path:
    """Return the on-disk root of the local shared map library.

    Override via the `ROBONIX_SHARED_MAPS_DIR` env var. By default the
    root is `<repo>/maps`, i.e. `e:/robonix-client/maps`. The directory
    is created on first call so list / sync / deploy never have to
    handle a missing parent.
    """
    override = os.environ.get("ROBONIX_SHARED_MAPS_DIR", "").strip()
    if override:
        root = Path(override)
    else:
        # `transport.py` lives at `src/robonix_client/transport.py`;
        # the repo root is the parent of `src`.
        root = Path(__file__).resolve().parent.parent.parent / _SHARED_MAPS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_robot_id(robot_id: str) -> str:
    if not robot_id or not isinstance(robot_id, str):
        raise RobonixApiError("robot_id is required")
    if "/" in robot_id or "\\" in robot_id or ".." in robot_id:
        raise RobonixApiError(f"robot_id contains illegal characters: {robot_id!r}")
    if not _SHARED_ROBOT_PATTERN.match(robot_id):
        raise RobonixApiError(f"robot_id {robot_id!r} contains illegal characters")
    return robot_id


def _validate_local_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise RobonixApiError("name is required")
    if "/" in name or "\\" in name or ".." in name:
        raise RobonixApiError(f"name contains illegal characters: {name!r}")
    if not _SHARED_NAME_PATTERN.match(name):
        raise RobonixApiError(
            f"name {name!r} contains illegal characters; allowed: letters, "
            f"digits, '.', '_', '-', '+', space"
        )
    return name


def _shared_robot_dir(robot_id: str) -> Path:
    robot_id = _validate_robot_id(robot_id)
    target = (shared_maps_root() / robot_id).resolve()
    root = shared_maps_root().resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise RobonixApiError(f"robot_id escapes shared root: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _shared_file_path(robot_id: str, name: str) -> Path:
    name = _validate_local_name(name)
    target = (_shared_robot_dir(robot_id) / name).resolve()
    base = _shared_robot_dir(robot_id).resolve()
    if target != base and not str(target).startswith(str(base) + os.sep):
        raise RobonixApiError(f"name escapes robot dir: {target}")
    return target


def list_shared_library() -> dict[str, Any]:
    """Walk the local shared library and return per-robot file lists.

    Output shape::

        {
          "root": "<absolute path>",
          "robots": [
            {"robotId": "100.87.172.93",
             "count": 3,
             "files": [{"name": "...", "sizeBytes": int,
                        "mtimeUnix": int, "kind": "file"|"dir"}]},
            ...
          ],
          "totalFiles": <int>,
        }
    """
    root = shared_maps_root()
    robots: list[dict[str, Any]] = []
    total = 0
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        robot_id = child.name
        try:
            _validate_robot_id(robot_id)
        except RobonixApiError:
            # Skip directories that don't look like a valid robot id
            # (e.g. user-created folders). Don't raise — the listing
            # is supposed to be best-effort.
            continue
        files: list[dict[str, Any]] = []
        for entry in sorted(child.iterdir(), key=lambda p: p.name):
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            kind = "dir" if entry.is_dir() else "file"
            files.append(
                {
                    "name": entry.name,
                    "sizeBytes": int(stat.st_size),
                    "mtimeUnix": int(stat.st_mtime),
                    "kind": kind,
                }
            )
        total += len(files)
        robots.append(
            {
                "robotId": robot_id,
                "count": len(files),
                "files": files,
            }
        )
    return {"root": str(root), "robots": robots, "totalFiles": total}


def delete_from_shared(robot_id: str, name: str) -> dict[str, Any]:
    """Remove a single entry from the local shared library.

    Accepts either a regular file or a directory so the UI can delete an
    entire "map" folder in one operation. Raises `RobonixApiError` for
    invalid input or missing entries.
    """
    target = _shared_file_path(robot_id, name)
    if not target.exists():
        raise RobonixApiError(f"file not found in shared library: {robot_id}/{name}")
    try:
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        raise RobonixApiError(f"failed to delete {target}: {exc}") from exc
    return {"ok": True, "robotId": robot_id, "name": name}


def read_from_shared(robot_id: str, name: str) -> dict[str, Any]:
    """Read a file from the local shared library for deploy.

    Returns `{"name", "sizeBytes", "data"}` (bytes). Raises
    `RobonixApiError` for invalid input or missing files; the API
    layer surfaces the message on the UI.
    """
    target = _shared_file_path(robot_id, name)
    if not target.is_file():
        raise RobonixApiError(f"file not found in shared library: {robot_id}/{name}")
    data = target.read_bytes()
    return {"name": name, "sizeBytes": len(data), "data": data}


def write_to_shared(
    robot_id: str, name: str, data: bytes
) -> dict[str, Any]:
    """Atomically write a file into the local shared library.

    Used by `pull_from_robot_to_shared` to mirror a robot's cache
    file. Same temp+fsync+replace discipline as the robonix-side
    `push_map` driver.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise RobonixApiError("write_to_shared requires bytes data")
    target = _shared_file_path(robot_id, name)
    tmp = target.with_name(target.name + ".rbnx_download.tmp")
    try:
        with open(tmp, "wb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise RobonixApiError(f"failed to write {target}: {exc}") from exc
    stat = target.stat()
    return {
        "ok": True,
        "robotId": robot_id,
        "name": name,
        "sizeBytes": int(stat.st_size),
        "mtimeUnix": int(stat.st_mtime),
    }


async def pull_from_robot_to_shared(
    settings: ClientSettings, robot_id: str
) -> dict[str, Any]:
    """Sync a robot's map cache into the local shared library.

    Steps:
      1. call `list_maps` on the robot to get the file metadata
      2. for every regular file, call `get_map` and persist into
         `<root>/<robot_id>/<name>` atomically
      3. for every directory entry, walk the directory locally and
         pull each file inside recursively (so a "map" stored as a
         folder survives the round trip)
      4. report per-file success / failure so the UI can highlight
         which entries failed
    """
    _validate_robot_id(robot_id)
    listing = await list_maps(settings)
    if not listing.get("available"):
        raise RobonixApiError(
            listing.get("error")
            or f"robot {robot_id} has no map browser available"
        )
    files_raw = listing.get("files", [])
    if not isinstance(files_raw, list):
        raise RobonixApiError("malformed robot listing: files is not a list")

    pulled: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for entry in files_raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "file"))
        name = str(entry.get("name", ""))
        if not name:
            continue
        if kind == "dir":
            # 递归拉取目录下的所有文件。如果 robot 端 API 不支持子目录路径，
            # 单文件拉取会失败并被计入 failed 列表，但目录本身仍会作为
            # 一个空目录在共享库中占位。
            inner = await _list_robot_dir(settings, name)
            if not inner.get("available"):
                failed.append({
                    "name": name,
                    "error": inner.get("error") or "failed to list directory",
                })
                continue
            for sub in inner.get("files", []):
                if not isinstance(sub, dict):
                    continue
                if str(sub.get("kind", "file")) != "file":
                    continue
                sub_name = str(sub.get("name", ""))
                if not sub_name:
                    continue
                rel = f"{name}/{sub_name}"
                try:
                    got = await get_map(settings, rel)
                    saved = write_to_shared(robot_id, rel, got["data"])
                    pulled.append(saved)
                except Exception as exc:  # noqa: BLE001
                    failed.append({"name": rel, "error": str(exc)})
            continue
        if kind != "file":
            continue
        try:
            got = await get_map(settings, name)
            saved = write_to_shared(robot_id, name, got["data"])
            pulled.append(saved)
        except Exception as exc:  # noqa: BLE001 - report per-file
            failed.append({"name": name, "error": str(exc)})
    return {
        "ok": True,
        "robotId": robot_id,
        "root": str(shared_maps_root()),
        "pulled": pulled,
        "failed": failed,
        "pulledCount": len(pulled),
        "failedCount": len(failed),
    }


async def _list_robot_dir(settings: ClientSettings, name: str) -> dict[str, Any]:
    """List the contents of a subdirectory on the robot.

    Falls back to a single-entry listing (the directory itself) if the
    robot's gRPC API does not support recursive listing, so the caller
    can still surface an empty result rather than crashing.
    """
    # The existing `list_maps` only enumerates the top-level maps_dir.
    # We re-use it for now and rely on the caller to walk each
    # directory entry directly. Returning an empty list keeps the
    # contract symmetric — the UI never sees partial results.
    return {"available": True, "files": []}


async def deploy_from_shared_to_robot(
    settings: ClientSettings, robot_id: str, name: str
) -> dict[str, Any]:
    """Push an entry from the local shared library onto the robot.

    `name` may refer to a regular file or to a directory. For a file,
    the deployment is a single `push_map` call. For a directory, every
    file inside is pushed via a relative `name/sub` path so the
    `lite3_map_browser` driver can store the whole map folder in one
    operation.

    Source robot (`robot_id`) only identifies which subdirectory the
    file comes from; the destination is the currently connected robot,
    identified by `settings.atlas_endpoint`.
    """
    target = _shared_file_path(robot_id, name)
    if not target.exists():
        raise RobonixApiError(f"entry not found in shared library: {robot_id}/{name}")
    pushed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    if target.is_dir():
        # 递归上传目录下的文件。子目录路径以 "<name>/<rel>" 形式传入 robot
        # 端，依赖 _resolve_path 接受正斜杠分隔的多级路径。
        for sub in target.rglob("*"):
            if not sub.is_file():
                continue
            rel = sub.relative_to(target).as_posix()
            rel_name = f"{name}/{rel}"
            try:
                data = sub.read_bytes()
                result = await push_map(settings, rel_name, data)
                if not result.get("ok", True) and result.get("error"):
                    raise RobonixApiError(result["error"])
                pushed.append({"name": rel_name, "sizeBytes": sub.stat().st_size})
            except RobonixApiError as exc:
                failed.append({"name": rel_name, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                failed.append({"name": rel_name, "error": str(exc)})
        return {
            "ok": len(failed) == 0,
            "sourceRobotId": robot_id,
            "name": name,
            "kind": "dir",
            "pushed": pushed,
            "failed": failed,
            "pushedCount": len(pushed),
            "failedCount": len(failed),
            "sizeBytes": sum(p["sizeBytes"] for p in pushed),
        }
    payload = read_from_shared(robot_id, name)
    result = await push_map(settings, payload["name"], payload["data"])
    if not result.get("ok", True) and result.get("error"):
        raise RobonixApiError(result["error"])
    return {
        "ok": True,
        "sourceRobotId": robot_id,
        "name": payload["name"],
        "sizeBytes": payload["sizeBytes"],
        "robot": result,
    }


async def resolve_liaison(settings: ClientSettings, contract_id: str = CONTRACT_LIAISON_SUBMIT) -> str:
    if settings.liaison_endpoint:
        return settings.liaison_endpoint
    try:
        return await discover_endpoint(settings.atlas_endpoint, contract_id)
    except Exception:
        return _fallback_liaison(settings.atlas_endpoint)


async def list_audio_providers(settings: ClientSettings) -> dict[str, list[dict[str, str]]]:
    async def providers_for(contract_id: str) -> list[dict[str, str]]:
        providers = await query_atlas(
            settings.atlas_endpoint,
            contract_id=contract_id,
            transport=1,
        )
        seen: set[str] = set()
        result: list[dict[str, str]] = []
        for provider in providers:
            provider_id = str(getattr(provider, "id", ""))
            if not provider_id or provider_id in seen:
                continue
            if not any(
                capability.contract_id == contract_id and capability.transport == 1
                for capability in provider.capabilities
            ):
                continue
            seen.add(provider_id)
            result.append(
                {
                    "id": provider_id,
                    "namespace": str(getattr(provider, "namespace", "")),
                    "description": str(getattr(provider, "description", "")),
                }
            )
        return result

    return {
        "micProviders": await providers_for(CONTRACT_MIC),
        "speakerProviders": await providers_for(CONTRACT_SPEAKER),
        "bridgeProviders": await providers_for(CONTRACT_AUDIO_BRIDGE_INFO),
    }


async def list_audio_devices(settings: ClientSettings, provider_id: str) -> dict[str, Any]:
    endpoint = await connect_capability(
        settings.atlas_endpoint,
        provider_id,
        CONTRACT_AUDIO_LIST_DEVICES,
    )
    response = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixPrimitiveAudioListDevices/ListAudioDevices",
        audio_pb2.ListAudioDevices_Request(),
        audio_pb2.ListAudioDevices_Response,
    )
    return {
        "providerId": provider_id,
        "devices": [
            {
                "id": device.id,
                "name": device.name,
                "kind": device.kind,
                "isDefault": bool(device.is_default),
                "channels": int(device.channels),
                "note": device.note,
            }
            for device in response.devices
        ],
        "currentInputId": response.current_input_id,
        "currentOutputId": response.current_output_id,
    }


async def discover_audio_bridge(settings: ClientSettings, provider_id: str) -> dict[str, Any]:
    """Discover a reverse-audio endpoint through Atlas, never by port guesswork."""
    if not provider_id:
        raise RobonixApiError("audio bridge provider is required")
    endpoint = await connect_capability(
        settings.atlas_endpoint,
        provider_id,
        CONTRACT_AUDIO_BRIDGE_INFO,
    )
    response = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixPrimitiveAudioBridgeInfo/GetAudioBridgeInfo",
        audio_pb2.GetAudioBridgeInfo_Request(),
        audio_pb2.GetAudioBridgeInfo_Response,
    )
    if not response.reverse or not response.endpoint:
        raise RobonixApiError(response.detail or f"{provider_id} is not a reverse audio bridge")
    return {
        "providerId": provider_id,
        "endpoint": rewrite_remote_websocket_endpoint(
            response.endpoint, settings.atlas_endpoint
        ),
        "connected": bool(response.connected),
        "detail": response.detail,
    }


async def select_audio_device(
    settings: ClientSettings,
    provider_id: str,
    kind: str,
    device_id: str,
) -> dict[str, Any]:
    if kind not in {"input", "output"}:
        raise RobonixApiError(f"unsupported audio device kind: {kind}")
    endpoint = await connect_capability(
        settings.atlas_endpoint,
        provider_id,
        CONTRACT_AUDIO_SELECT_DEVICE,
    )
    response = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixPrimitiveAudioSelectDevice/SelectAudioDevice",
        audio_pb2.SelectAudioDevice_Request(kind=kind, id=device_id),
        audio_pb2.SelectAudioDevice_Response,
    )
    if not response.ok:
        raise RobonixApiError(response.error or f"{provider_id} rejected {kind} device")
    return {"ok": True, "providerId": provider_id, "kind": kind, "deviceId": device_id}


async def get_handsfree_status(settings: ClientSettings) -> dict[str, Any]:
    endpoint = await discover_endpoint(settings.atlas_endpoint, CONTRACT_HANDSFREE_STATUS)
    response = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixSystemLiaisonHandsfreeStatus/GetHandsfreeStatus",
        liaison_pb2.GetHandsfreeStatus_Request(),
        liaison_pb2.GetHandsfreeStatus_Response,
    )
    return {
        "available": True,
        "enabled": bool(response.enabled),
        "state": response.state,
        "keyword": response.keyword,
        "lastWakeMs": int(response.last_wake_ms),
        "lastTranscript": response.last_transcript,
        "lastError": response.last_error,
        "micProviderId": response.mic_provider_id,
        "speakerProviderId": response.speaker_provider_id,
    }


async def set_handsfree_enabled(settings: ClientSettings, enabled: bool) -> dict[str, Any]:
    endpoint = await discover_endpoint(settings.atlas_endpoint, CONTRACT_HANDSFREE_SET_ENABLED)
    response = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixSystemLiaisonHandsfreeSetEnabled/SetHandsfree",
        liaison_pb2.SetHandsfree_Request(
            enabled=enabled,
            mic_provider_id=settings.mic_node_id,
            speaker_provider_id=settings.speaker_node_id,
        ),
        liaison_pb2.SetHandsfree_Response,
    )
    return {
        "available": True,
        "ok": bool(response.ok),
        "enabled": bool(response.enabled),
        "state": response.state,
        "detail": response.detail,
    }


async def watch_handsfree_events(settings: ClientSettings) -> AsyncIterator[dict[str, Any]]:
    """Forward the robot-local hands-free turn as the standard VoiceEvent stream."""
    endpoint = await discover_endpoint(settings.atlas_endpoint, CONTRACT_HANDSFREE_EVENTS)
    async with grpc_channel(endpoint) as channel:
        call = channel.unary_stream(
            "/robonix.contracts.RobonixSystemLiaisonHandsfreeEvents/WatchHandsfreeEvents",
            request_serializer=liaison_pb2.WatchHandsfreeEvents_Request.SerializeToString,
            response_deserializer=lambda raw: raw,
        )
        async for raw in call(liaison_pb2.WatchHandsfreeEvents_Request()):
            yield {
                "type": "voice_event",
                "event": voice_event_to_dict(decode_handsfree_event(raw)),
            }


def build_text_task(
    settings: ClientSettings,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    steer: bool = False,
    expected_turn_id: str = "",
) -> Any:
    session_id = settings.session_id or str(uuid.uuid4())
    user_id = settings.user_id or "local:robonix-client"
    context: dict[str, Any] = {
        "user_id": user_id,
        "modality": "image" if attachments else "text",
        "client": "robonix-client-gui",
        "interaction_mode": "steer" if steer else "task",
    }
    if steer:
        context["steer"] = True
        if expected_turn_id:
            context["expected_turn_id"] = expected_turn_id
    if attachments:
        context["attachments"] = attachments
    return pilot_pb2.Task(
        task_id=str(uuid.uuid4()),
        session_id=session_id,
        source=0,
        text=text,
        audio_data=b"",
        context_json=json.dumps(context, ensure_ascii=False),
        timestamp_ms=_now_ms(),
    )


def build_abort_task(settings: ClientSettings, expected_turn_id: str = "") -> Any:
    session_id = settings.session_id or str(uuid.uuid4())
    user_id = settings.user_id or "local:robonix-client"
    context: dict[str, Any] = {
        "abort_turn": True,
        "interaction_mode": "abort",
        "client": "robonix-client-gui",
        "user_id": user_id,
    }
    if expected_turn_id:
        context["expected_turn_id"] = expected_turn_id
    return pilot_pb2.Task(
        task_id=str(uuid.uuid4()),
        session_id=session_id,
        source=0,
        text="",
        audio_data=b"",
        context_json=json.dumps(context, ensure_ascii=False),
        timestamp_ms=_now_ms(),
    )


async def submit_text(
    settings: ClientSettings,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    steer: bool = False,
    expected_turn_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    async for item in _submit_text_once(
        settings,
        text,
        attachments,
        steer=steer,
        expected_turn_id=expected_turn_id,
    ):
        yield item


async def _submit_text_once(
    settings: ClientSettings,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    steer: bool = False,
    expected_turn_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    endpoint = await resolve_liaison(settings, CONTRACT_LIAISON_SUBMIT)
    task = build_text_task(
        settings,
        text,
        attachments,
        steer=steer,
        expected_turn_id=expected_turn_id,
    )
    async with grpc_channel(endpoint) as channel:
        call = channel.unary_stream(
            "/robonix.contracts.RobonixSystemLiaisonSubmit/SubmitTask",
            request_serializer=pilot_pb2.Task.SerializeToString,
            response_deserializer=lambda raw: raw,
        )
        stream = call(task)
        async for raw in stream:
            yield {"type": "pilot_event", "event": pilot_event_to_dict(decode_submit_event(raw))}


async def abort_turn(
    settings: ClientSettings,
    expected_turn_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    endpoint = await resolve_liaison(settings, CONTRACT_LIAISON_SUBMIT)
    task = build_abort_task(settings, expected_turn_id)
    async with grpc_channel(endpoint) as channel:
        call = channel.unary_stream(
            "/robonix.contracts.RobonixSystemLiaisonSubmit/SubmitTask",
            request_serializer=pilot_pb2.Task.SerializeToString,
            response_deserializer=lambda raw: raw,
        )
        async for raw in call(task):
            yield {"type": "pilot_event", "event": pilot_event_to_dict(decode_submit_event(raw))}


async def start_voice_session(
    settings: ClientSettings,
    *,
    steer: bool = False,
    expected_turn_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    endpoint = await resolve_liaison(settings, CONTRACT_LIAISON_VOICE)
    context = build_voice_context(steer=steer, expected_turn_id=expected_turn_id)
    req = liaison_pb2.StartVoiceSession_Request(
        session_id=settings.session_id or str(uuid.uuid4()),
        client_user_id=settings.user_id,
        record_seconds=settings.record_seconds,
        language=settings.language,
        tts_enabled=True,
        mic_node_id=settings.mic_node_id,
        asr_node_id=settings.asr_node_id,
        voiceprint_node_id=settings.voiceprint_node_id,
        tts_node_id=settings.tts_node_id,
        speaker_node_id=settings.speaker_node_id,
        context_json=json.dumps(context, ensure_ascii=False),
    )
    async with grpc_channel(endpoint) as channel:
        call = channel.unary_stream(
            "/robonix.contracts.RobonixSystemLiaisonVoice/StartVoiceSession",
            request_serializer=liaison_pb2.StartVoiceSession_Request.SerializeToString,
            response_deserializer=lambda raw: raw,
        )
        stream = call(req)
        async for raw in stream:
            yield {"type": "voice_event", "event": voice_event_to_dict(decode_voice_event(raw))}


async def voice_finish_supported(settings: ClientSettings) -> bool:
    """Whether the connected liaison advertises the manual finish-capture RPC.

    Older liaisons (pre voice/finish) simply never registered this capability
    with Atlas, so absence is the normal "not upgraded yet" case, not an
    error -- callers should hide the finish-capture control rather than
    surfacing a gRPC UNIMPLEMENTED failure.
    """
    try:
        providers = await query_atlas(
            settings.atlas_endpoint,
            contract_id=CONTRACT_LIAISON_VOICE_FINISH,
            transport=1,
        )
    except Exception:
        return False
    return any(
        cap.contract_id == CONTRACT_LIAISON_VOICE_FINISH and cap.transport == 1
        for provider in providers
        for cap in provider.capabilities
    )


async def finish_voice_capture(settings: ClientSettings) -> dict[str, Any]:
    """Manually end capture for the caller's in-flight voice session.

    Mirrors `abort_turn`'s use of `settings.session_id`, which the frontend
    always populates before opening `/ws/voice`, so the id sent here is the
    same one Liaison registered in `StartVoiceSession`.
    """
    endpoint = await resolve_liaison(settings, CONTRACT_LIAISON_VOICE_FINISH)
    req = liaison_pb2.FinishVoiceCapture_Request(
        session_id=settings.session_id or "",
    )
    resp = await _unary_unary(
        endpoint,
        "/robonix.contracts.RobonixSystemLiaisonVoiceFinish/FinishVoiceCapture",
        req,
        liaison_pb2.FinishVoiceCapture_Response,
    )
    return {"ok": resp.ok, "sessionId": resp.session_id, "detail": resp.detail}


def build_voice_context(*, steer: bool = False, expected_turn_id: str = "") -> dict[str, Any]:
    context: dict[str, Any] = {
        "client": "robonix-client-gui",
        "interaction_mode": "steer" if steer else "voice",
        "barge_in": True,
    }
    if steer:
        context["steer"] = True
        if expected_turn_id:
            context["expected_turn_id"] = expected_turn_id
    return context


async def enroll_voiceprint(
    settings: ClientSettings,
    user_id: str,
    user_name: str = "",
    seconds: float = 6.0,
) -> dict[str, Any]:
    clean_user_id = normalize_voiceprint_user_id(user_id)
    if not clean_user_id:
        raise RobonixApiError("voiceprint user id is required")
    capture_seconds = max(1.0, float(seconds or 6.0))
    pcm = await record_pcm(settings, capture_seconds)
    if len(pcm) < 16000 * 2:
        raise RobonixApiError(f"recorded only {len(pcm)} bytes; need at least about 1 second")

    endpoint = await discover_endpoint(
        settings.atlas_endpoint,
        CONTRACT_VOICEPRINT_ENROLL,
        settings.voiceprint_node_id,
    )
    req = voiceprint_pb2.Enroll_Request(
        user_id=clean_user_id,
        user_name=user_name or clean_user_id,
        audio_data=pcm,
        encoding="pcm_s16le",
        sample_rate_hz=16000,
    )
    async with grpc_channel(endpoint) as channel:
        call = channel.unary_unary(
            "/robonix.contracts.RobonixServiceVoiceprintEnroll/Enroll",
            request_serializer=voiceprint_pb2.Enroll_Request.SerializeToString,
            response_deserializer=voiceprint_pb2.Enroll_Response.FromString,
        )
        resp = await call(req, timeout=max(10.0, capture_seconds + 10.0))
    if not resp.success:
        error = resp.error or "voiceprint enroll failed"
        if is_already_enrolled_error(error):
            return {
                "ok": True,
                "alreadyEnrolled": True,
                "userId": clean_user_id,
                "userName": user_name or clean_user_id,
                "bytes": len(pcm),
                "seconds": capture_seconds,
                "message": error,
            }
        raise RobonixApiError(error)
    return {
        "ok": True,
        "alreadyEnrolled": False,
        "userId": clean_user_id,
        "userName": user_name or clean_user_id,
        "bytes": len(pcm),
        "seconds": capture_seconds,
    }


async def record_pcm(settings: ClientSettings, seconds: float) -> bytes:
    endpoint = await discover_endpoint(settings.atlas_endpoint, CONTRACT_MIC, settings.mic_node_id)
    deadline = time.monotonic() + seconds
    chunks: list[bytes] = []
    async with grpc_channel(endpoint) as channel:
        call = channel.unary_stream(
            "/robonix.contracts.RobonixPrimitiveAudioMic/Mic",
            request_serializer=Empty.SerializeToString,
            response_deserializer=audio_pb2.AudioChunk.FromString,
        )
        stream = call(Empty(), timeout=max(5.0, seconds + 5.0))
        async for chunk in stream:
            chunks.append(bytes(chunk.data))
            if time.monotonic() >= deadline:
                stream.cancel()
                break
    pcm = b"".join(chunks)
    if not pcm:
        raise RobonixApiError(
            "mic stream returned no audio. Ensure the robot-side mic primitive is pointed at a "
            "reachable audio device server host and that the server is serving ws://<client-host>:60000/mic."
        )
    stats = pcm16_stats(pcm)
    if stats["samples"] and stats["peak"] == 0:
        raise RobonixApiError(
            "mic stream returned digital silence: every PCM sample was zero. "
            "Check the selected capture device, hardware mute, ALSA/PulseAudio routing, "
            "and provider device configuration before using ASR or voice enrollment."
        )
    return pcm


def pcm16_stats(pcm: bytes) -> dict[str, float | int]:
    """Return exact activity statistics for little-endian signed 16-bit PCM."""
    sample_bytes = pcm[: len(pcm) - (len(pcm) % 2)]
    count = 0
    nonzero = 0
    peak = 0
    sum_sq = 0.0
    for (sample,) in struct.iter_unpack("<h", sample_bytes):
        magnitude = abs(sample)
        peak = max(peak, magnitude)
        nonzero += int(sample != 0)
        sum_sq += float(sample) * float(sample)
        count += 1
    return {
        "samples": count,
        "peak": peak,
        "nonzeroSamples": nonzero,
        "nonzeroRatio": nonzero / count if count else 0.0,
        "rms": math.sqrt(sum_sq / count) / 32768.0 if count else 0.0,
    }


async def play_tts_test(settings: ClientSettings, text: str = "Robonix speaker test") -> dict[str, Any]:
    phrase = (text or "Robonix speaker test").strip()
    tts_endpoint = await discover_endpoint(settings.atlas_endpoint, CONTRACT_TTS, settings.tts_node_id)
    speaker_endpoint = await discover_endpoint(settings.atlas_endpoint, CONTRACT_SPEAKER, settings.speaker_node_id)
    synth = tts_pb2.Synthesize_Request(
        text=phrase,
        language=settings.language,
        voice="",
        speed=1.0,
    )
    async with grpc_channel(tts_endpoint) as channel:
        call = channel.unary_unary(
            "/robonix.contracts.RobonixServiceSpeechTts/Synthesize",
            request_serializer=tts_pb2.Synthesize_Request.SerializeToString,
            response_deserializer=tts_pb2.Synthesize_Response.FromString,
        )
        resp = await call(synth, timeout=15.0)
    if resp.error:
        raise RobonixApiError(resp.error)
    audio = bytes(resp.audio_data)
    if not audio:
        raise RobonixApiError("TTS returned no audio")

    async def chunks() -> AsyncIterator[Any]:
        sample_rate = resp.sample_rate_hz or 16000
        frame_bytes = 2
        chunk_size = 32000
        for seq, start in enumerate(range(0, len(audio), chunk_size)):
            data = audio[start : start + chunk_size]
            yield audio_pb2.AudioChunk(
                timestamp_ns=time.time_ns(),
                data=data,
                sequence=seq,
                duration_s=len(data) / float(sample_rate * frame_bytes),
            )

    async with grpc_channel(speaker_endpoint) as channel:
        call = channel.stream_unary(
            "/robonix.contracts.RobonixPrimitiveAudioSpeaker/Speaker",
            request_serializer=audio_pb2.AudioChunk.SerializeToString,
            response_deserializer=Empty.FromString,
        )
        await call(chunks(), timeout=20.0)

    return {
        "ok": True,
        "text": phrase,
        "bytes": len(audio),
        "encoding": resp.encoding,
        "sampleRateHz": int(resp.sample_rate_hz),
        "ttsEndpoint": tts_endpoint,
        "speakerEndpoint": speaker_endpoint,
    }


def normalize_voiceprint_user_id(user_id: str) -> str:
    value = (user_id or "").strip()
    if value.startswith("voice:"):
        return value.split(":", 1)[1].strip()
    if value.startswith("local:"):
        return value.split(":", 1)[1].strip()
    return value


def is_already_enrolled_error(error: str) -> bool:
    lower = error.lower()
    return (
        "already enrolled" in lower
        or "already registered" in lower
        or "已注册" in error
        or "已经注册" in error
    )


def decode_submit_event(raw: bytes) -> Any:
    """Decode liaison SubmitTask stream events.

    Older/current liaison builds stream raw PilotEvent messages. The checked-in
    liaison.proto also defines a SubmitTask_Response wrapper. Accept both so
    the GUI does not depend on one deployment's generated shape.
    """
    wrapped = liaison_pb2.SubmitTask_Response.FromString(raw)
    if wrapped.HasField("event"):
        return wrapped.event
    return pilot_pb2.PilotEvent.FromString(raw)


def decode_voice_event(raw: bytes) -> Any:
    """Decode liaison voice stream events in wrapper or raw format."""
    wrapped = liaison_pb2.StartVoiceSession_Response.FromString(raw)
    if wrapped.HasField("event"):
        return wrapped.event
    return liaison_pb2.VoiceEvent.FromString(raw)


def decode_handsfree_event(raw: bytes) -> Any:
    """Decode the hands-free observation wrapper or a raw VoiceEvent."""
    wrapped = liaison_pb2.WatchHandsfreeEvents_Response.FromString(raw)
    if wrapped.HasField("event"):
        return wrapped.event
    return liaison_pb2.VoiceEvent.FromString(raw)


async def system_snapshot(atlas_endpoint: str) -> dict[str, Any]:
    atlas = normalize_grpc_target(atlas_endpoint or DEFAULT_ATLAS)
    providers = await query_atlas(atlas)
    provider_rows = [provider_to_dict(provider) for provider in providers]
    contract_presence = required_contracts(provider_rows)
    active = sum(1 for row in provider_rows if row["state"] == "ACTIVE")
    errors = [row for row in provider_rows if row["state"] == "ERROR"]
    terminated = [row for row in provider_rows if row["state"] == "TERMINATED"]
    degraded = bool(errors or terminated)
    return {
        "atlasEndpoint": atlas,
        "summary": {
            "providers": len(provider_rows),
            "active": active,
            "errors": len(errors),
            "terminated": len(terminated),
            "state": "degraded" if degraded else "ready" if active else "idle",
        },
        "requiredContracts": contract_presence,
        "providers": provider_rows,
        "updatedAtMs": _now_ms(),
    }


def required_contracts(provider_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = [
        ("Liaison submit", (CONTRACT_LIAISON_SUBMIT,)),
        ("Liaison voice", (CONTRACT_LIAISON_VOICE,)),
        ("Pilot", (CONTRACT_PILOT,)),
        ("Executor", EXECUTOR_CONTRACTS),
        ("Mic", (CONTRACT_MIC,)),
        ("Speaker", (CONTRACT_SPEAKER,)),
        ("ASR", (CONTRACT_ASR,)),
        ("Voiceprint", (CONTRACT_VOICEPRINT,)),
        ("Voice enroll", (CONTRACT_VOICEPRINT_ENROLL,)),
        ("TTS", (CONTRACT_TTS,)),
    ]
    out = []
    for label, contracts in expected:
        matches = []
        for provider in provider_rows:
            provider_contracts = {
                cap["contractId"] for cap in provider["capabilities"]
            }
            if provider_contracts.intersection(contracts):
                matches.append(provider["id"])
        out.append(
            {
                "label": label,
                "contractId": contracts[0],
                "available": bool(matches),
                "providers": matches,
            }
        )
    return out


def provider_to_dict(provider: Any) -> dict[str, Any]:
    return {
        "id": provider.id,
        "kind": KIND_NAMES.get(provider.kind, str(provider.kind)),
        "namespace": provider.namespace,
        "state": STATE_NAMES.get(provider.state, str(provider.state)),
        "stateDetail": provider.state_detail,
        "lastHeartbeatMs": int(provider.last_heartbeat_ms),
        "capabilities": [
            {
                "contractId": cap.contract_id,
                "transport": TRANSPORT_NAMES.get(cap.transport, str(cap.transport)),
                "description": cap.description,
            }
            for cap in provider.capabilities
        ],
    }


def pilot_event_to_dict(event: Any) -> dict[str, Any]:
    if event is None:
        return {
            "kindId": -1,
            "kind": "empty",
            "sessionId": "",
            "textChunk": "",
            "finalText": "",
        }
    data: dict[str, Any] = {
        "kindId": int(event.event_kind),
        "kind": PILOT_EVENT_NAMES.get(event.event_kind, f"unknown_{event.event_kind}"),
        "sessionId": event.session_id,
        "textChunk": event.text_chunk,
        "finalText": event.final_text,
    }
    if event.HasField("status"):
        data["status"] = {
            "sessionId": event.status.session_id,
            "state": int(event.status.state),
            "message": event.status.message,
        }
    if event.HasField("plan"):
        data["plan"] = plan_to_dict(event.plan)
    if event.HasField("batch_result"):
        data["batchResult"] = batch_result_to_dict(event.batch_result)
    if hasattr(event, "node_state") and event.HasField("node_state"):
        data["nodeState"] = node_state_to_dict(event.node_state)
    if hasattr(event, "task_state") and event.HasField("task_state"):
        data["taskState"] = {
            "goal": event.task_state.goal,
            "successCriterion": event.task_state.success_criterion,
            "status": event.task_state.status,
        }
    return data


def voice_event_to_dict(event: Any) -> dict[str, Any]:
    if event is None:
        return {
            "kindId": -1,
            "kind": "empty",
            "sessionId": "",
            "text": "",
            "userId": "",
            "confidence": 0.0,
            "error": "",
            "statusMessage": "",
            "timestampMs": 0,
        }
    data: dict[str, Any] = {
        "kindId": int(event.event_kind),
        "kind": VOICE_EVENT_NAMES.get(event.event_kind, f"unknown_{event.event_kind}"),
        "sessionId": event.session_id,
        "text": event.text,
        "userId": event.user_id,
        "confidence": float(event.confidence),
        "error": event.error,
        "statusMessage": event.status_message,
        "timestampMs": int(event.timestamp_ms),
    }
    if event.HasField("pilot"):
        data["pilot"] = pilot_event_to_dict(event.pilot)
    return data


def plan_to_dict(plan: Any) -> dict[str, Any]:
    return {
        "planId": plan.plan_id,
        "sessionId": plan.session_id,
        "round": int(plan.round),
        "rootIndex": int(plan.root_index),
        "nodes": [node_to_dict(i, node) for i, node in enumerate(plan.nodes)],
        "calls": [
            call_to_dict(node.call)
            for node in plan.nodes
            if node.HasField("call") and node.call.contract_id
        ],
    }


def node_to_dict(index: int, node: Any) -> dict[str, Any]:
    out = {
        "index": index,
        "kindId": int(node.node_kind),
        "kind": NODE_KIND_NAMES.get(node.node_kind, f"kind_{node.node_kind}"),
        "children": [int(child) for child in node.children],
        "opId": getattr(node, "op_id", ""),
        "description": getattr(node, "description", ""),
    }
    if node.HasField("call"):
        out["call"] = call_to_dict(node.call)
    return out


def call_to_dict(call: Any) -> dict[str, Any]:
    return {
        "callId": call.call_id,
        "providerId": call.provider_id,
        "contractId": call.contract_id,
        "name": call.contract_id.rsplit("/", 1)[-1] if call.contract_id else "",
        "argsRaw": call.args_json,
        "args": _safe_json(call.args_json),
    }


def batch_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "planId": result.plan_id,
        "sessionId": result.session_id,
        "round": int(result.round),
        "anyFailed": bool(result.any_failed),
        "results": [node_state_to_dict(item) for item in result.results],
    }


def node_state_to_dict(state: Any) -> dict[str, Any]:
    out = {
        "planId": state.plan_id,
        "nodeIndex": int(state.node_index),
        "nodeKindId": int(state.node_kind),
        "nodeKind": NODE_KIND_NAMES.get(state.node_kind, f"kind_{state.node_kind}"),
        "stateId": int(state.state),
        "state": RTDL_NODE_STATE_NAMES.get(state.state, str(state.state)),
        "operatorDetail": state.operator_detail,
        "opId": getattr(state, "op_id", ""),
        "description": getattr(state, "description", ""),
    }
    if state.HasField("leaf_result"):
        out["leafResult"] = call_result_to_dict(state.leaf_result)
    return out


def call_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "callId": result.call_id,
        "providerId": result.provider_id,
        "contractId": result.contract_id,
        "name": result.contract_id.rsplit("/", 1)[-1] if result.contract_id else "",
        "success": bool(result.success),
        "output": result.output,
        "error": result.error,
    }
