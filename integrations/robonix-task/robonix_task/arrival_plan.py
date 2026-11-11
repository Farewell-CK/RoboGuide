"""Export an already admitted MI plan for the restricted manual arrival publisher.

Run on the Controller host with ``mission/src`` on PYTHONPATH. This helper uses
the actual MI codec and verifies the original accepted wire digest. Its caller
must supply authenticated original admission and same-Mission tenant projections.
It performs no HTTP, admission, action or publication and grants no authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ArrivalPlanError(ValueError):
    """Refuse an export detached from the original admission and tenant authority."""


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    """Bound and detach finite JSON input without retaining caller-owned containers."""
    if not isinstance(value, dict):
        raise ArrivalPlanError("original_json_object_required")
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > MAX_BYTES:
            raise ArrivalPlanError("document_exceeds_2mib")
        result: dict[str, Any] = json.loads(raw)
        return result
    except (TypeError, UnicodeError, ValueError, OverflowError, RecursionError) as error:
        raise ArrivalPlanError("bounded_finite_json_required") from error


def encode_plan_for_arrival(
    plan_document: dict[str, Any], admission: dict[str, Any], tenant: dict[str, Any]
) -> bytes:
    """Recreate exact MI bytes only when the original wire-admission digest matches.

    Explicit nonzero revision metadata is rejected. The current tenant endpoint
    provides no revision; the arrival publisher independently checks the current
    Mission revision, AwaitingSatisfaction and complete attempts before publishing.
    Missing metadata is never upgraded into current physical or execution truth.

    ``source_plan_digest`` is an opaque qualified token from the authenticated
    tenant projection: Core hashes its internal domain plan, whereas MI's draft
    canonical digest hashes a different public document. They are not comparable.
    This offline exporter checks token syntax and same-Mission identity, not its
    derivation or tenant authorization. The publisher reads the actual token again
    and Core validates it when admitting evidence; this helper never publishes it.
    """
    from mission.models import MissionPlan
    from mission.submission_evidence import ControllerAdmissionEvidence

    document, original_admission, original_tenant = map(_copy, (plan_document, admission, tenant))
    for value in (original_admission, original_tenant):
        if "plan_revision" in value and (
            type(value["plan_revision"]) is not int or value["plan_revision"] != 0
        ):
            raise ArrivalPlanError("original_revision_zero_required")
    original_admission.pop("plan_revision", None)
    if set(original_tenant) - {"mission_id", "tenant_id", "source_plan_digest", "plan_revision"}:
        raise ArrivalPlanError("unknown_original_tenant_projection")
    try:
        receipt = ControllerAdmissionEvidence.from_json(original_admission)
        plan = MissionPlan.from_json(document)
        normalized = plan.to_json()
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ArrivalPlanError("original_plan_or_admission_invalid") from error
    tenant_id = original_tenant.get("tenant_id")
    digest = original_tenant.get("source_plan_digest")
    if (
        receipt.mission_id != plan.mission.mission_id
        or original_tenant.get("mission_id") != receipt.mission_id
        or not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or tenant_id != tenant_id.strip()
        or len(tenant_id) > 256
        or "*" in tenant_id
        or any(ord(character) < 32 for character in tenant_id)
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        raise ArrivalPlanError("original_mission_tenant_or_digest_mismatch")
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise ArrivalPlanError("document_exceeds_2mib")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != receipt.accepted_request_body_sha256:
        raise ArrivalPlanError("original_accepted_wire_bytes_unrecoverable")
    return raw


def save_plan(
    plan_document: dict[str, Any], admission: dict[str, Any], tenant: dict[str, Any], path: Path
) -> Path:
    """Write one checked export exclusively without replacing any previous evidence.

    Parent directories must already exist. A failed write leaves its own partial
    file for explicit inspection; rerunning cannot silently overwrite that file.
    """
    raw = encode_plan_for_arrival(plan_document, admission, tenant)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return path
