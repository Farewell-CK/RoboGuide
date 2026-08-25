"""Local-only bridge from roboguide-node to the installed Robonix Python SDK."""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import grpc
from robonix_api import ATLAS
from robonix_api.atlas_types import Transport

_CLIENTS: dict[str, tuple[Any, Any, Any]] = {}


def _capability(contract_id: str) -> Any:
    """Return the unique active capability or raise without guessing a provider."""
    capabilities = ATLAS.find_capability(contract_id=contract_id)
    if len(capabilities) != 1:
        raise RuntimeError(f"expected one provider for {contract_id}, found {len(capabilities)}")
    return capabilities[0]


def _stub(contract_id: str, class_name: str) -> Any:
    """Return a process-lifetime cached Robonix capability client."""
    import robonix_contracts_pb2_grpc as contracts_grpc

    cached = _CLIENTS.get(contract_id)
    if cached is not None:
        return cached[2]
    capability = _capability(contract_id)
    edge = ATLAS.connect_capability(
        consumer_id="roboguide-node",
        provider_id=capability.provider_id,
        contract_id=contract_id,
        transport=Transport.GRPC,
    )
    endpoint = edge.endpoint.removeprefix("http://").removeprefix("https://")
    channel = grpc.insecure_channel(endpoint)
    stub = getattr(contracts_grpc, class_name)(channel)
    _CLIENTS[contract_id] = (edge, channel, stub)
    return stub


def discover(_: dict[str, Any]) -> list[str]:
    """Return active Atlas capability contract identities."""
    return sorted(cap.contract_id for cap in ATLAS.find_capability())


def health(_: dict[str, Any]) -> dict[str, str]:
    """Report Atlas reachability without claiming task completion."""
    ATLAS.find_capability()
    return {"health": "online", "detail": "Robonix Atlas reachable"}


def reach_region(payload: dict[str, Any]) -> dict[str, str]:
    """Resolve a region through Scene then submit the resulting pose to Navigation."""
    import geometry_msgs_pb2
    import navigation_pb2
    import semantic_map_pb2

    scene = _stub("robonix/system/scene/goal_room", "RobonixSystemSceneGoalRoomStub")
    goal = scene.GoalRoom(semantic_map_pb2.GoalRoom_Request(room_id=payload["region_id"]))
    if not goal.reachable:
        raise RuntimeError(goal.reason or "region is not reachable")
    pose = geometry_msgs_pb2.PoseStamped()
    pose.pose.position.x = goal.x
    pose.pose.position.y = goal.y
    pose.pose.orientation.z = math.sin(goal.yaw / 2.0)
    pose.pose.orientation.w = math.cos(goal.yaw / 2.0)
    nav = _stub("robonix/service/navigation/navigate", "RobonixServiceNavigationNavigateStub")
    result = nav.Navigate(navigation_pb2.Navigate_Request(goal=pose))
    if not result.accepted:
        raise RuntimeError(result.detail or "navigation rejected")
    return {"run_id": result.run_id}


def navigation_status(payload: dict[str, Any]) -> dict[str, str]:
    """Read one navigation run through its status sub-contract."""
    import navigation_pb2

    stub = _stub(
        "robonix/service/navigation/navigate/status", "RobonixServiceNavigationNavigateStatusStub"
    )
    result = stub.GetNavigationStatus(
        navigation_pb2.GetNavigationStatus_Request(run_id=payload["run_id"])
    )
    return {"state": result.state if result.known else "UNKNOWN", "detail": result.detail}


def cancel_navigation(payload: dict[str, Any]) -> dict[str, bool]:
    """Cancel one navigation run through its cancel sub-contract."""
    import navigation_pb2

    stub = _stub(
        "robonix/service/navigation/navigate/cancel", "RobonixServiceNavigationNavigateCancelStub"
    )
    result = stub.CancelNavigation(
        navigation_pb2.CancelNavigation_Request(run_id=payload["run_id"])
    )
    if not result.accepted:
        raise RuntimeError(result.detail or "navigation cancellation rejected")
    return {"accepted": True}


def _handlers() -> dict[str, Any]:
    """Return the fixed local operation table."""
    return {
        "discover": discover,
        "health": health,
        "reach_region": reach_region,
        "navigation_status": navigation_status,
        "cancel_navigation": cancel_navigation,
    }


def serve() -> int:
    """Serve ordered JSON-lines IPC while retaining Robonix clients."""
    handlers = _handlers()
    for line in sys.stdin:
        request = json.loads(line)
        response: dict[str, Any] = {"id": request.get("id")}
        try:
            response["result"] = handlers[request["operation"]](request["payload"])
        except Exception as error:  # noqa: BLE001 - convert local SDK failures to IPC errors
            response["error"] = str(error)
        print(json.dumps(response), flush=True)
    return 0


def main() -> int:
    """Start the long-lived bridge or dispatch a legacy one-shot call."""
    if sys.argv[1:] == ["--serve"]:
        return serve()
    handlers = {
        **_handlers(),
    }
    operation = sys.argv[1]
    payload = json.loads(sys.argv[2])
    print(json.dumps(handlers[operation](payload)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
