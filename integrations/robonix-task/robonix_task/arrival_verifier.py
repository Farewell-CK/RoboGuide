"""Publish explicit onsite arrival reports through the existing generic verifier ingress.

The human report concerns arrival-and-stopped at one exact named target. It does not
prove automatic localization, device-stop/recovery or grant movement authority. No
background process, Pilot access, registry mutation, action or retry exists here.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import re
import sqlite3
import ssl
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from robonix_task.arrival_verifier_store import (
    ArrivalVerificationError as ArrivalVerificationError,
)
from robonix_task.arrival_verifier_store import (
    ArrivalVerifierStore,
    digest,
    encode,
)
from robonix_task.patrol import ARRIVAL_CONTRACT, MAX_ARRIVAL_AGE_MS

CONFIG_SCHEMA = "roboguide.onsite-arrival-verifier/v0.1"
CONFIRMATION_SCHEMA = "roboguide.onsite-arrival-confirmation/v0.1"
MAX_BYTES = 2 * 1024 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REFERENCE = {"entity_id", "node_id", "target_name", "map_id", "region_id", "region_source_digest"}
_CONFIRMATION_FIELDS = {
    "schema_version",
    "observation_id",
    "tenant_id",
    "mission_id",
    "task_id",
    "plan_digest",
    "wire_plan_digest",
    "attempts",
    "entity_id",
    "node_id",
    "predicate",
    "observer_id",
    "verdict",
}


def _text(value: Any, *, limit: int = 256) -> bool:
    """Require an exact bounded reference without wildcard or control-character authority."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= limit
        and "*" not in value
        and all(ord(c) >= 32 for c in value)
    )


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON members instead of silently changing accepted plan semantics."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArrivalVerificationError("duplicate_json_member")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    """Reject nonfinite JSON constants even when the standard decoder would accept them."""
    raise ArrivalVerificationError("nonfinite_json:" + value)


def _json(raw: bytes) -> dict[str, Any]:
    """Decode a bounded strict object while preserving separately hashed original bytes."""
    if len(raw) > MAX_BYTES:
        raise ArrivalVerificationError("document_exceeds_2mib")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ArrivalVerificationError("json_object_required")
        encode(value)
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise ArrivalVerificationError("invalid_json_document") from error


def _one(values: Any, key: str, expected: str) -> dict[str, Any]:
    """Select one original object and fail on missing, malformed or duplicate entries."""
    if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
        raise ArrivalVerificationError("original_object_collection_required")
    selected = [v for v in values if v.get(key) == expected]
    if len(selected) != 1:
        raise ArrivalVerificationError("original_object_missing_or_ambiguous:" + key)
    return dict(selected[0])


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Freeze one narrowly scoped manual source and its exact original target references."""
    config = _json(encode(config).encode())
    if (
        set(config)
        != {
            "schema_version",
            "source_id",
            "revision",
            "tenant_id",
            "producer_principal",
            "verifier",
            "max_age_ms",
            "targets",
        }
        or config.get("schema_version") != CONFIG_SCHEMA
        or any(not _text(config.get(k)) for k in ("source_id", "tenant_id", "producer_principal"))
        or type(config.get("revision")) is not int
        or not 0 < config["revision"] <= 9223372036854775807
        or config.get("verifier") != ARRIVAL_CONTRACT
        or type(config.get("max_age_ms")) is not int
        or config["max_age_ms"] != MAX_ARRIVAL_AGE_MS
        or not isinstance(config.get("targets"), list)
        or not 1 <= len(config["targets"]) <= 64
    ):
        raise ArrivalVerificationError("exact_manual_arrival_source_configuration_required")
    predicates: set[str] = set()
    routes: dict[str, str] = {}
    for target in config["targets"]:
        if (
            not isinstance(target, dict)
            or set(target) != _REFERENCE | {"predicate"}
            or any(not _text(target.get(k)) for k in _REFERENCE)
            or not _DIGEST.fullmatch(target["region_source_digest"])
        ):
            raise ArrivalVerificationError("exact_target_reference_required")
        reference = {key: target[key] for key in _REFERENCE}
        expected = (
            "onsite-arrival/v0.1 "
            + target["entity_id"]
            + " arrived-and-stopped at "
            + target["target_name"]
            + " target-reference-"
            + digest(reference)
        )
        if (
            target.get("predicate") != expected
            or expected in predicates
            or routes.get(target["entity_id"], target["node_id"]) != target["node_id"]
            or any(e != target["entity_id"] and n == target["node_id"] for e, n in routes.items())
        ):
            raise ArrivalVerificationError("target_predicate_digest_or_route_mismatch")
        predicates.add(expected)
        routes[target["entity_id"]] = target["node_id"]
    return config


class ArrivalController(Protocol):
    """Expose original Mission read projections and the sole evidence publication route."""

    def arrival_view(self, mission_id: str, *, timeout_ms: int) -> dict[str, Any]:
        """Read tenant, admission, Mission and complete current-attempt projections."""
        ...

    def publish_evidence(self, evidence: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        """POST once and return the original authenticated receipt wrapper."""
        ...


class HttpArrivalController:
    """Use dedicated mTLS credentials without proxy, redirects, retries or general HTTP access."""

    def __init__(self, origin: str, *, ca: Path, certificate: Path, private_key: Path) -> None:
        """Load independently provisioned verifier credentials without printing their values."""
        parsed = urllib.parse.urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ArrivalVerificationError("explicit_https_controller_origin_required")
        self.context = ssl.create_default_context(cafile=str(ca))
        self.context.load_cert_chain(str(certificate), str(private_key))
        self.hostname, self.port = parsed.hostname, parsed.port

    def _seconds_remaining(self, started: int, timeout_ms: int) -> float:
        """Apply one total monotonic transport deadline rather than a renewed per-read timeout."""
        elapsed_ns = time.monotonic_ns() - started
        remaining = timeout_ms / 1000 - elapsed_ns / 1_000_000_000
        if elapsed_ns < 0 or remaining <= 0:
            raise ArrivalVerificationError("manual_report_transport_budget_expired")
        return remaining

    def _request(
        self, path: str, *, timeout_ms: int, evidence: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform one bounded exact-route request without uncertain transport recovery."""
        allowed = re.fullmatch(
            r"/v1/missions/[^/]+(?:/(?:tenant|admission|execution-attempts))?", path
        )
        if (
            type(timeout_ms) is not int
            or not 0 < timeout_ms <= MAX_ARRIVAL_AGE_MS
            or (evidence is None and allowed is None)
            or (evidence is not None and path != "/v1/verifier-evidence")
        ):
            raise ArrivalVerificationError("restricted_verifier_transport_route_or_budget")
        started = time.monotonic_ns()
        body = None if evidence is None else encode(evidence).encode()
        connection = http.client.HTTPSConnection(
            self.hostname, self.port, timeout=timeout_ms / 1000, context=self.context
        )
        try:
            # A late DNS/TLS connection cannot proceed to the evidence POST.
            connection.connect()
            assert connection.sock is not None
            transport_socket = connection.sock
            transport_socket.settimeout(self._seconds_remaining(started, timeout_ms))
            connection.request(
                "GET" if evidence is None else "POST",
                path,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body) if body is not None else 0),
                },
            )
            transport_socket.settimeout(self._seconds_remaining(started, timeout_ms))
            response = connection.getresponse()
            if response.status != 200:  # Includes every redirect; never follow a Location.
                raise ArrivalVerificationError("unexpected_controller_response_status")
            chunks, size = [], 0
            while True:
                if response.isclosed():
                    break
                transport_socket.settimeout(self._seconds_remaining(started, timeout_ms))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                self._seconds_remaining(started, timeout_ms)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ArrivalVerificationError("document_exceeds_2mib")
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            connection.close()
        return _json(raw)

    def arrival_view(self, mission_id: str, *, timeout_ms: int) -> dict[str, Any]:
        """Share one monotonic deadline among the four authorized original Mission GETs."""
        if not _text(mission_id):
            raise ArrivalVerificationError("exact_mission_id_required")
        start = time.monotonic_ns()
        base = "/v1/missions/" + urllib.parse.quote(mission_id, safe="")
        result: dict[str, Any] = {}
        for name, suffix in (
            ("tenant", "/tenant"),
            ("admission", "/admission"),
            ("attempts", "/execution-attempts"),
            ("mission", ""),
        ):
            remaining = timeout_ms - math.ceil((time.monotonic_ns() - start) / 1_000_000)
            value = self._request(base + suffix, timeout_ms=remaining)
            result[name] = value.get("attempts") if name == "attempts" else value
        return result

    def publish_evidence(self, evidence: dict[str, Any], *, timeout_ms: int) -> dict[str, Any]:
        """Send once through the only allowed write endpoint."""
        return self._request("/v1/verifier-evidence", timeout_ms=timeout_ms, evidence=evidence)


class ArrivalVerifier:
    """Bind a human report to original admission and the complete current attempt set."""

    def __init__(
        self,
        config: dict[str, Any],
        controller: ArrivalController,
        store: ArrivalVerifierStore | None,
        *,
        wall_clock_ms: Callable[[], int] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        """Freeze source scope and inject clocks for actuator-free software acceptance."""
        self.config = validate_config(config)
        self.controller, self.store = controller, store
        self.wall_clock_ms = wall_clock_ms or _wall_clock_ms
        self.monotonic_ns = monotonic_ns or time.monotonic_ns

    def _remaining(self, started: int) -> int:
        """Charge every validation, read, journal and transport delay conservatively."""
        delta = self.monotonic_ns() - started
        remaining = MAX_ARRIVAL_AGE_MS - math.ceil(delta / 1_000_000)
        if delta < 0 or remaining <= 0:
            raise ArrivalVerificationError("manual_report_publication_budget_expired")
        return int(remaining)

    def inspect(self, plan_bytes: bytes, mission_id: str, task_id: str) -> dict[str, Any]:
        """Read a pending exact binding without publishing evidence or changing a journal."""
        start = self.monotonic_ns()
        view = self.controller.arrival_view(mission_id, timeout_ms=self._remaining(start))
        result = self._binding(plan_bytes, mission_id, task_id, view)
        self._remaining(start)
        return result

    def _binding(
        self, raw: bytes, mission_id: str, task_id: str, view: dict[str, Any]
    ) -> dict[str, Any]:
        """Require original bytes, fixed physical Actor and every completed original role."""
        try:
            plan = _json(raw)
            mission, tenant, admission = view["mission"], view["tenant"], view["admission"]
            wire_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
            if (
                not _text(mission_id)
                or not _text(task_id)
                or plan.get("schema_version") != "roboguide.mission-plan/v0.10"
                or plan["mission"]["id"] != mission_id
                or any(d["mission_id"] != mission_id for d in (mission, tenant, admission))
                or tenant["tenant_id"] != self.config["tenant_id"]
                or not isinstance(tenant.get("source_plan_digest"), str)
                or not _DIGEST.fullmatch(tenant["source_plan_digest"])
                or admission["accepted_request_body_sha256"] != wire_digest
                or type(mission.get("plan_revision")) is not int
                or mission["plan_revision"] != 0
                or not _text(mission.get("group_id"))
                or mission["group_id"] != admission["group_id"]
            ):
                raise ArrivalVerificationError("original_admission_tenant_or_plan_mismatch")
            task = _one(plan["tasks"], "id", task_id)
            live = _one(mission["tasks"], "task_id", task_id)
            satisfaction = task["satisfaction"]
            specification = satisfaction["verifier"]
            predicate = specification["predicate"]
            target = _one(self.config["targets"], "predicate", predicate)
            if (
                live["status"] != "AwaitingSatisfaction"
                or live["context_id"] != task["context_id"]
                or satisfaction["basis"] != "verifier-evidence"
                or satisfaction["expected_effect"] != predicate
                or specification["contract"] != ARRIVAL_CONTRACT
                or type(specification["max_evidence_age_ms"]) is not int
                or specification["max_evidence_age_ms"] != MAX_ARRIVAL_AGE_MS
            ):
                raise ArrivalVerificationError("pending_exact_arrival_satisfaction_required")
            context = _one(plan["contexts"], "id", task["context_id"])
            roles = task["roles"]
            if not isinstance(roles, list) or not roles or len(roles) > 64:
                raise ArrivalVerificationError("complete_original_role_set_required")
            role_ids = [r["id"] for r in roles]
            if len(set(role_ids)) != len(role_ids) or any(not _text(r) for r in role_ids):
                raise ArrivalVerificationError("distinct_original_roles_required")
            for role in roles:
                context_role = _one(context["roles"], "id", role["context_role"])
                actor = _one(plan["mission"]["actors"], "id", context_role["actor"])
                if actor.get("physical_entity") != target["entity_id"]:
                    raise ArrivalVerificationError("fixed_physical_actor_mismatch")
            attempts = view["attempts"]
            if not isinstance(attempts, list) or any(not isinstance(a, dict) for a in attempts):
                raise ArrivalVerificationError("complete_attempt_projection_required")
            selected = [a for a in attempts if a.get("task_id") == task_id]
            if (
                len(selected) != len(role_ids)
                or {a["role_id"] for a in selected} != set(role_ids)
                or any(
                    a["status"] != "Completed"
                    or a["mission_id"] != mission_id
                    or a["group_id"] != mission["group_id"]
                    or a["node_id"] != target["node_id"]
                    or not _text(a["execution_id"])
                    for a in selected
                )
                or len({a["execution_id"] for a in selected}) != len(selected)
            ):
                raise ArrivalVerificationError("complete_current_completed_attempt_set_required")
            return {
                "tenant_id": self.config["tenant_id"],
                "mission_id": mission_id,
                "task_id": task_id,
                "plan_digest": tenant["source_plan_digest"],
                "wire_plan_digest": wire_digest,
                "attempts": sorted(
                    [{"role_id": a["role_id"], "attempt_id": a["execution_id"]} for a in selected],
                    key=lambda a: a["role_id"],
                ),
                "entity_id": target["entity_id"],
                "node_id": target["node_id"],
                "predicate": predicate,
            }
        except (KeyError, TypeError, AttributeError) as error:
            raise ArrivalVerificationError("malformed_original_binding") from error

    def publish(self, plan_bytes: bytes, confirmation: dict[str, Any]) -> dict[str, Any]:
        """Publish a fresh explicit report once and retain uncertain delivery across restart."""
        started, observed = self.monotonic_ns(), self.wall_clock_ms()
        confirmation = _json(encode(confirmation).encode())
        if (
            self.store is None
            or set(confirmation) != _CONFIRMATION_FIELDS
            or confirmation.get("schema_version") != CONFIRMATION_SCHEMA
            or any(
                not _text(confirmation.get(k))
                for k in (
                    "observation_id",
                    "tenant_id",
                    "mission_id",
                    "task_id",
                    "entity_id",
                    "node_id",
                    "observer_id",
                )
            )
            or not _text(confirmation.get("predicate"), limit=4096)
            or confirmation.get("verdict") not in {"arrived-and-stopped", "unknown"}
            or type(observed) is not int
            or observed < 0
        ):
            raise ArrivalVerificationError("explicit_exact_onsite_confirmation_required")
        original = {
            "confirmation": confirmation,
            "source_config": self.config,
            "accepted_wire_plan_sha256": "sha256:" + hashlib.sha256(plan_bytes).hexdigest(),
            "source_time_basis": "manual-report-received; not robot measurement time",
        }
        prior = self.store.get(confirmation["observation_id"])
        if prior is not None:
            if prior["original"] != original:
                raise ArrivalVerificationError("observation_id_conflicts_with_original_input")
            if prior["status"] == "publishing":
                raise ArrivalVerificationError("delivery_uncertain_manual_reconciliation_required")
            return prior
        view = self.controller.arrival_view(
            confirmation["mission_id"], timeout_ms=self._remaining(started)
        )
        binding = self._binding(
            plan_bytes, confirmation["mission_id"], confirmation["task_id"], view
        )
        if any(confirmation[key] != value for key, value in binding.items()):
            raise ArrivalVerificationError("manual_confirmation_binding_mismatch")
        remaining = self._remaining(started)
        evidence = (
            None
            if confirmation["verdict"] == "unknown"
            else {
                "evidence_id": confirmation["observation_id"],
                "source_id": self.config["source_id"],
                "source_revision": self.config["revision"],
                "tenant_id": binding["tenant_id"],
                "task_ref": {"mission_id": binding["mission_id"], "task_id": binding["task_id"]},
                "plan_digest": binding["plan_digest"],
                "attempts": binding["attempts"],
                "predicate": binding["predicate"],
                "verdict": "satisfied",
                "finality": "final",
                "source_observed_at_ms": observed,
                "valid_for_ms": remaining // 2,
                "sequence": 0,
                "artifact_digest": None,
                "artifact_size_bytes": None,
            }
        )
        if evidence is not None and evidence["valid_for_ms"] <= 0:
            raise ArrivalVerificationError("manual_report_publication_budget_expired")
        saved = self.store.prepare(original, evidence, received_at_ms=observed)
        if evidence is None:
            return saved
        try:
            # Keep frozen validity plus ALL receive-to-response delay within five seconds.
            send_budget = self._remaining(started) - saved["evidence"]["valid_for_ms"]
            if send_budget <= 0:
                raise ArrivalVerificationError("manual_report_publication_budget_expired")
        except ArrivalVerificationError:
            self.store.finish(confirmation["observation_id"], None)
            raise
        response = self.controller.publish_evidence(saved["evidence"], timeout_ms=send_budget)
        receipt = response.get("receipt")
        if (
            response.get("schema_version") != "roboguide.semantic-verifier-receipt/v0.1"
            or not isinstance(receipt, dict)
            or receipt.get("evidence") != saved["evidence"]
            or receipt.get("authority_subject") != self.config["producer_principal"]
        ):
            raise ArrivalVerificationError("unqualified_verifier_receipt_delivery_uncertain")
        return self.store.finish(confirmation["observation_id"], response)


def _wall_clock_ms() -> int:
    """Timestamp receipt of a human report without claiming a device measurement clock."""
    return time.time_ns() // 1_000_000


def _read(path: Path) -> bytes:
    """Read one bounded document without loading arbitrary-size files into memory."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ArrivalVerificationError("document_exceeds_2mib")
    return raw


def main(argv: list[str] | None = None) -> int:
    """Inspect or publish one report without registry, action, cancellation or daemon commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "publish"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--ca", required=True, type=Path)
    parser.add_argument("--certificate", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--mission-id")
    parser.add_argument("--task-id")
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--confirm-arrived-and-stopped", action="store_true")
    args = parser.parse_args(argv)
    try:
        config, plan = _json(_read(args.config)), _read(args.plan)
        controller = HttpArrivalController(
            args.controller, ca=args.ca, certificate=args.certificate, private_key=args.private_key
        )
        if args.operation == "inspect":
            if (
                not args.mission_id
                or not args.task_id
                or args.confirmation
                or args.journal
                or args.confirm_arrived_and_stopped
            ):
                raise ArrivalVerificationError("inspect_requires_exact_mission_and_task_only")
            result = ArrivalVerifier(config, controller, None).inspect(
                plan, args.mission_id, args.task_id
            )
        else:
            if not args.confirmation or not args.journal or args.mission_id or args.task_id:
                raise ArrivalVerificationError("publish_requires_exact_confirmation_and_journal")
            confirmation = _json(_read(args.confirmation))
            if (
                confirmation.get("verdict") == "arrived-and-stopped"
                and not args.confirm_arrived_and_stopped
            ):
                raise ArrivalVerificationError(
                    "explicit_human_arrived_and_stopped_confirmation_required"
                )
            verifier = ArrivalVerifier(config, controller, ArrivalVerifierStore(args.journal))
            result = verifier.publish(plan, confirmation)
        print(encode(result))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        # Transport exception text can contain credential paths; never echo it.
        reason = str(error) if isinstance(error, ArrivalVerificationError) else type(error).__name__
        print(encode({"status": "refused", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
