"""Typed read-only Controller inventory and accepted-plan submission boundary."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from http.client import HTTPException, HTTPMessage
from math import isfinite
from typing import Any, Protocol, cast
from urllib.parse import quote, urlparse

from mission.deployment_assessment import InitialOperationAssessment
from mission.models import JSONObject, MissionPlan
from mission.submission_evidence import ControllerAdmissionEvidence, ControllerSubmissionEvidence

MAX_CONTROLLER_RESPONSE_BYTES = 2 * 1024 * 1024
INVENTORY_SCHEMA = "roboguide.inventory/v0.1"


class MissionControllerError(RuntimeError):
    """Report an invalid Controller response or bounded transport failure."""

    submission_evidence: ControllerSubmissionEvidence | None = None


@dataclass(frozen=True, slots=True)
class InventoryCapability:
    """Describe one coarse capability's current advisory availability."""

    kind: str
    available: bool

    @classmethod
    def from_json(cls, value: object) -> InventoryCapability:
        """Parse one capability fact while rejecting unknown wire shapes."""
        if not isinstance(value, dict) or set(value) != {"kind", "available"}:
            raise MissionControllerError("inventory capability fields do not match v0.1")
        kind = value["kind"]
        available = value["available"]
        if (
            not isinstance(kind, str)
            or kind not in {"mobility", "transport", "compute", "observation"}
            or not isinstance(available, bool)
        ):
            raise MissionControllerError("inventory capability values are invalid")
        return cls(kind=kind, available=available)

    def to_json(self) -> JSONObject:
        """Serialize one model-facing capability fact without granting authority."""
        return {"kind": self.kind, "available": self.available}


@dataclass(frozen=True, slots=True)
class InventoryResource:
    """Describe one registered resource's identity, kind, and advisory capacity."""

    resource_id: str
    kind: str
    capacity: int

    @classmethod
    def from_json(cls, value: object) -> InventoryResource:
        """Parse one resource fact while rejecting invalid capacity or missing identity."""
        if not isinstance(value, dict) or set(value) != {"resource_id", "kind", "capacity"}:
            raise MissionControllerError("inventory resource fields do not match v0.1")
        resource_id = value["resource_id"]
        kind = value["kind"]
        capacity = value["capacity"]
        if (
            not isinstance(resource_id, str)
            or not resource_id
            or not isinstance(kind, str)
            or kind not in {"space", "compute", "time"}
            or isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 0
        ):
            raise MissionControllerError("inventory resource values are invalid")
        return cls(resource_id=resource_id, kind=kind, capacity=capacity)

    def to_json(self) -> JSONObject:
        """Serialize one model-facing resource fact without implying reservation."""
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "capacity": self.capacity,
        }


@dataclass(frozen=True, slots=True)
class InventoryNode:
    """Expose one node's advisory registration, health, liveness, and contract facts."""

    node_id: str
    reported_health: str
    source_observed_at_ms: int
    received_at_ms: int
    liveness: str
    liveness_observed_at_ms: int
    capabilities: tuple[InventoryCapability, ...]
    contracts: tuple[str, ...]
    resources: tuple[InventoryResource, ...]

    @classmethod
    def from_json(cls, value: object) -> InventoryNode:
        """Parse one inventory node while rejecting missing or malformed planning facts."""
        expected = {
            "node_id",
            "reported_health",
            "source_observed_at_ms",
            "received_at_ms",
            "liveness",
            "liveness_observed_at_ms",
            "capabilities",
            "contracts",
            "resources",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise MissionControllerError("inventory node fields do not match v0.1")
        node_id = value.get("node_id")
        health = value.get("reported_health")
        liveness = value.get("liveness")
        source_observed_at_ms = value.get("source_observed_at_ms")
        received_at_ms = value.get("received_at_ms")
        liveness_observed_at_ms = value.get("liveness_observed_at_ms")
        capabilities = value.get("capabilities")
        contracts = value.get("contracts")
        resources = value.get("resources")
        if not all(isinstance(item, str) and item for item in (node_id, health, liveness)):
            raise MissionControllerError("inventory node identity and status must be text")
        if health not in {"Online", "Degraded", "Offline", "SafeStopped"}:
            raise MissionControllerError("inventory reported_health is unsupported")
        if liveness not in {"Reachable", "Unreachable"}:
            raise MissionControllerError("inventory liveness is unsupported")
        timestamps = (source_observed_at_ms, received_at_ms, liveness_observed_at_ms)
        if not all(
            not isinstance(timestamp, bool) and isinstance(timestamp, int) and timestamp >= 0
            for timestamp in timestamps
        ):
            raise MissionControllerError("inventory node timestamps must be nonnegative integers")
        if not isinstance(capabilities, list):
            raise MissionControllerError("inventory capabilities must be an array")
        if not isinstance(contracts, list) or not all(
            isinstance(contract, str) and contract for contract in contracts
        ):
            raise MissionControllerError("inventory contracts must be text")
        if not isinstance(resources, list):
            raise MissionControllerError("inventory resources must be an array")
        return cls(
            node_id=cast(str, node_id),
            reported_health=cast(str, health),
            source_observed_at_ms=cast(int, source_observed_at_ms),
            received_at_ms=cast(int, received_at_ms),
            liveness=cast(str, liveness),
            liveness_observed_at_ms=cast(int, liveness_observed_at_ms),
            capabilities=tuple(
                InventoryCapability.from_json(capability) for capability in capabilities
            ),
            contracts=tuple(cast(list[str], contracts)),
            resources=tuple(InventoryResource.from_json(resource) for resource in resources),
        )

    def to_json(self) -> JSONObject:
        """Serialize bounded deployment facts without granting Mission admission authority."""
        return {
            "node_id": self.node_id,
            "reported_health": self.reported_health,
            "source_observed_at_ms": self.source_observed_at_ms,
            "received_at_ms": self.received_at_ms,
            "liveness": self.liveness,
            "liveness_observed_at_ms": self.liveness_observed_at_ms,
            "capabilities": [capability.to_json() for capability in self.capabilities],
            "contracts": list(self.contracts),
            "resources": [resource.to_json() for resource in self.resources],
        }


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    """Contain one advisory Shared Node State snapshot returned by the Controller."""

    observed_at_ms: int
    nodes: tuple[InventoryNode, ...]

    @classmethod
    def from_json(cls, value: JSONObject) -> InventorySnapshot:
        """Parse the versioned inventory projection without granting it Control authority."""
        if set(value) != {"schema_version", "observed_at_ms", "nodes"}:
            raise MissionControllerError("inventory snapshot fields do not match v0.1")
        if value.get("schema_version") != INVENTORY_SCHEMA:
            raise MissionControllerError("Controller returned an unsupported inventory schema")
        observed_at_ms = value.get("observed_at_ms")
        nodes = value.get("nodes")
        if (
            isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms < 0
        ):
            raise MissionControllerError("inventory observed_at_ms must be a nonnegative integer")
        if not isinstance(nodes, list):
            raise MissionControllerError("inventory nodes must be an array")
        return cls(
            observed_at_ms=observed_at_ms,
            nodes=tuple(InventoryNode.from_json(node) for node in nodes),
        )

    def available_contracts(self) -> frozenset[str]:
        """Return contracts on healthy nodes with at least one currently available capability."""
        return frozenset(
            contract
            for node in self.nodes
            if node.reported_health in {"Online", "Degraded"} and node.liveness == "Reachable"
            if any(capability.available for capability in node.capabilities)
            for contract in node.contracts
        )

    def supports_requirement(
        self,
        capability_kind: str,
        contract: str,
        resource_requirements: tuple[tuple[str, int], ...],
    ) -> bool:
        """Check whether one node currently advertises all advisory role requirement facts."""
        return any(
            node.reported_health in {"Online", "Degraded"}
            and node.liveness == "Reachable"
            and contract in node.contracts
            and any(
                capability.kind == capability_kind and capability.available
                for capability in node.capabilities
            )
            and all(
                any(
                    resource.kind == kind and resource.capacity >= units
                    for resource in node.resources
                )
                for kind, units in resource_requirements
            )
            for node in self.nodes
        )

    def to_json(self) -> JSONObject:
        """Serialize a compact model-facing inventory without physical assignment decisions."""
        return {
            "schema_version": INVENTORY_SCHEMA,
            "observed_at_ms": self.observed_at_ms,
            "nodes": [node.to_json() for node in self.nodes],
        }


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    """Describe whether the Controller accepted one complete MissionPlan."""

    accepted: bool
    status_code: int
    detail: str
    evidence: ControllerSubmissionEvidence | None = None


class MissionPlanSubmitter(Protocol):
    """Submit semantically admitted plans without exposing deployment eligibility evidence."""

    def submit_plan(self, plan: MissionPlan) -> SubmissionReceipt:
        """Submit one complete plan and preserve Controller rejection details."""
        ...


class MissionController(MissionPlanSubmitter, Protocol):
    """Combine plan submission with a separate advisory deployment-view operation."""

    def inventory(self) -> InventorySnapshot:
        """Return the latest bounded inventory snapshot or raise a transport error."""
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject Controller redirects so deployment configuration remains authoritative."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        """Reject a server-selected target without issuing another request."""
        raise MissionControllerError(
            f"Controller returned redirect HTTP {code} for {request.full_url}"
        )


class HttpMissionController:
    """Use the bounded Controller HTTP API without owning execution lifecycle."""

    def __init__(self, endpoint: str, timeout_seconds: float) -> None:
        """Validate a fixed endpoint and retain bounded request settings."""
        parsed = urlparse(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise MissionControllerError("Controller endpoint must be a fixed HTTP(S) origin")
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise MissionControllerError("Controller timeout must be finite and positive")
        self._endpoint = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def inventory(self) -> InventorySnapshot:
        """Fetch and validate the current advisory Shared Node State projection."""
        status, decoded, _ = self._request("GET", "/v1/inventory", None)
        if status != 200:
            raise MissionControllerError(f"Controller inventory returned HTTP {status}")
        return InventorySnapshot.from_json(decoded)

    def submit_plan(self, plan: MissionPlan) -> SubmissionReceipt:
        """Submit a strict MissionPlan and classify accepted versus rejected responses."""
        return self.submit_plan_observed(plan, None)

    def assess_initial_support(self, plan: MissionPlan) -> InitialOperationAssessment:
        """Query support once without submitting a Mission or exposing inventory to a model."""
        status, decoded, _ = self._request(
            "POST", "/v1/missions/assess-initial-support", plan.to_json()
        )
        try:
            assessment = InitialOperationAssessment.from_json(decoded)
        except (TypeError, ValueError, KeyError) as error:
            raise MissionControllerError("Controller initial assessment is invalid") from error
        if status != 200 or not assessment.matches_plan(plan):
            raise MissionControllerError("Controller initial assessment is unavailable or detached")
        return assessment

    def submit_plan_observed(
        self,
        plan: MissionPlan,
        before_send: Callable[[ControllerSubmissionEvidence], None] | None,
    ) -> SubmissionReceipt:
        """Persist the exact prepared body before the transport can cause admission or dispatch."""
        status, decoded, evidence = self._request(
            "POST", "/v1/missions", plan.to_json(), before_send
        )
        if status in {200, 202} and (
            decoded.get("mission_id") != plan.mission.mission_id
            or not isinstance(decoded.get("group_id"), str)
            or not str(decoded["group_id"]).strip()
        ):
            failure = MissionControllerError("Controller acceptance identity is invalid")
            failure.submission_evidence = evidence
            raise failure
        detail_value = decoded.get("error", decoded.get("status", "Controller response"))
        detail = str(detail_value)
        return SubmissionReceipt(
            accepted=status in {200, 202},
            status_code=status,
            detail=detail,
            evidence=evidence,
        )

    def observe_admission(self, mission_id: str) -> ControllerAdmissionEvidence | None:
        """Read one digest-bound authority receipt; absent legacy receipts remain unavailable."""
        status, decoded, _ = self._request(
            "GET", f"/v1/missions/{quote(mission_id, safe='')}/admission", None
        )
        if status == 404:
            return None
        try:
            evidence = ControllerAdmissionEvidence.from_json(decoded)
        except (TypeError, ValueError) as error:
            raise MissionControllerError("Controller admission receipt is invalid") from error
        if status != 200 or evidence.mission_id != mission_id:
            raise MissionControllerError(
                "Controller admission receipt is unavailable or mismatched"
            )
        return evidence

    def observe_mission(self, mission_id: str) -> JSONObject:
        """Read the original Mission identity once without resubmitting or proving plan content.

        Even a 404 cannot prove that an earlier in-flight POST will never be
        accepted. This observation therefore never grants retry authority.
        """
        status, decoded, _ = self._request(
            "GET", f"/v1/missions/{quote(mission_id, safe='')}", None
        )
        if status == 404:
            return {
                "schema_version": "roboguide.controller-mission-observation/v0.1",
                "mission_id": mission_id,
                "lookup_result": "not_found",
                "status_code": status,
                "group_id": None,
                "mission_status": None,
            }
        group_id, mission_status = decoded.get("group_id"), decoded.get("status")
        if (
            status != 200
            or decoded.get("mission_id") != mission_id
            or not isinstance(group_id, str)
            or not group_id.strip()
            or len(group_id) > 256
            or not isinstance(mission_status, str)
            or not mission_status.strip()
            or len(mission_status) > 64
        ):
            raise MissionControllerError(
                "Controller Mission observation is unavailable or mismatched"
            )
        return {
            "schema_version": "roboguide.controller-mission-observation/v0.1",
            "mission_id": mission_id,
            "lookup_result": "found",
            "status_code": status,
            "group_id": group_id,
            "mission_status": mission_status,
        }

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None,
        before_send: Callable[[ControllerSubmissionEvidence], None] | None = None,
    ) -> tuple[int, JSONObject, ControllerSubmissionEvidence | None]:
        """Issue one bounded JSON request and return error responses without retrying."""
        payload = None
        headers = {"Accept": "application/json"}
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._endpoint}{path}", data=payload, headers=headers, method=method
        )
        evidence = (
            ControllerSubmissionEvidence.from_body(request.data, time.time_ns() // 1_000_000)
            if method == "POST" and path == "/v1/missions" and isinstance(request.data, bytes)
            else None
        )
        if evidence is not None and before_send is not None:
            before_send(evidence)
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                status = response.status
                raw = response.read(MAX_CONTROLLER_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read(MAX_CONTROLLER_RESPONSE_BYTES + 1)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            HTTPException,
            MissionControllerError,
        ) as error:
            failure = MissionControllerError(f"Controller request failed: {error}")
            failure.submission_evidence = (
                replace(evidence, transport_error=type(error).__name__) if evidence else None
            )
            raise failure from error
        if evidence is not None:
            evidence = replace(evidence, controller_status_code=status)
        if len(raw) > MAX_CONTROLLER_RESPONSE_BYTES:
            failure = MissionControllerError("Controller response exceeds local limit")
            failure.submission_evidence = evidence
            raise failure
        try:
            decoded: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            failure = MissionControllerError("Controller returned invalid JSON")
            failure.submission_evidence = evidence
            raise failure from error
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            failure = MissionControllerError("Controller response must be a JSON object")
            failure.submission_evidence = evidence
            raise failure
        if evidence is not None:
            evidence = replace(
                evidence,
                controller_mission_id=(
                    decoded["mission_id"] if isinstance(decoded.get("mission_id"), str) else None
                ),
                controller_group_id=(
                    decoded["group_id"] if isinstance(decoded.get("group_id"), str) else None
                ),
            )
        return status, cast(JSONObject, decoded), evidence
