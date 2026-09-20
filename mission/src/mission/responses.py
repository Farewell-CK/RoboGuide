"""Responses-compatible LLM adapters for Mission planning, review, and repair."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import MissionSettings
from mission.contract_values import MissionPlanError
from mission.execution_profile import DeploymentExecutionProfile
from mission.grounding_context import GroundingContextSnapshot, admitted_physical_entity_ids
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.provider_mission_plan import (
    build_mission_plan_provider_schema,
    normalize_mission_plan_provider_output,
)
from mission.rejected_draft import RejectedPlanError
from mission.request_record import DialogueTurn, IntentAssessment
from mission.review import MissionPlanReview
from mission.satisfaction_policy import MissionSatisfactionPolicy, validate_satisfaction_policy
from mission.semantic_evidence import semantic_goal_review_payload


class MissionProviderError(RuntimeError):
    """Report a transport, provider response, or model review failure."""


class JsonTransport(Protocol):
    """Send JSON requests behind an injectable network boundary."""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Post one JSON object and return one decoded JSON object."""
        ...


class UrllibJsonTransport:
    """Send bounded JSON POST requests using the Python standard library."""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Execute one request and convert HTTP or decoding failures into provider errors."""
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise MissionProviderError(f"provider returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise MissionProviderError(f"provider request failed: {error.reason}") from error
        decoded: object = json.loads(body)
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise MissionProviderError("provider response must be a JSON object")
        return cast(JSONObject, decoded)


def _validate_plan_output(
    value: JSONObject,
    mission_id: str,
    grounded_intent: GroundedIntent,
    capability_catalog: CanonicalCapabilityCatalog,
    satisfaction_policy: MissionSatisfactionPolicy | None,
    grounding_context: GroundingContextSnapshot,
    execution_profile: DeploymentExecutionProfile | None,
) -> MissionPlan:
    """Validate one generated draft against identity, implementation, and Catalog boundaries."""
    plan = MissionPlan.from_json(value)
    if execution_profile is not None:
        plan = execution_profile.apply(plan)
    plan.validate_implementation_support()
    plan.validate_physical_entity_grounding(admitted_physical_entity_ids(grounding_context))
    if plan.mission.mission_id != mission_id:
        raise MissionProviderError("model changed the requested mission id")
    if plan.mission.objective != grounded_intent.objective:
        raise MissionProviderError("model changed the requested mission objective")
    capability_catalog.validate_plan(plan)
    validate_satisfaction_policy(plan, satisfaction_policy)
    return plan


def _review_schema() -> JSONObject:
    """Return the strict provider schema for structured Mission review evidence."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["approved", "issues"],
        "properties": {
            "approved": {"type": "boolean"},
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "path", "message", "required_action"],
                    "properties": {
                        "code": {"type": "string"},
                        "path": {"type": "string"},
                        "message": {"type": "string"},
                        "required_action": {
                            "type": "string",
                            "enum": ["RepairPlan", "RequestClarification", "RejectDraft"],
                        },
                    },
                },
            },
        },
    }


def _with_semantic_goal(
    payload: JSONObject, grounding_context: GroundingContextSnapshot
) -> JSONObject:
    """Add explicit frozen-goal guidance without changing non-B1 provider inputs."""
    goal = semantic_goal_review_payload(grounding_context.semantic_evidence)
    if goal is None:
        return payload
    return {**payload, "authoritative_semantic_goal": goal}


class _ResponsesClient:
    """Own shared provider transport and strict structured-output mechanics only."""

    def __init__(
        self,
        settings: MissionSettings,
        environment: Mapping[str, str],
        transport: JsonTransport | None = None,
    ) -> None:
        """Validate runtime provider access and retain injectable request dependencies."""
        if settings.llm.network_access != "enabled":
            raise MissionProviderError("Mission LLM network access is disabled by configuration")
        self._settings = settings
        self._environment = environment
        self._transport = transport if transport is not None else UrllibJsonTransport()
        self._endpoint = settings.provider.endpoint(environment)
        self._api_key = settings.provider.api_key(environment)

    def _load_schema(self) -> JSONObject:
        """Load the configured JSON Schema used for strict provider output."""
        decoded: object = json.loads(self._settings.schema_path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise MissionProviderError("Mission Plan schema must be a JSON object")
        return cast(JSONObject, decoded)

    def _mission_plan_provider_schema(self, canonical_schema: JSONObject) -> JSONObject:
        """Return the current MissionPlan schema adapted to the strict provider DTO."""
        return build_mission_plan_provider_schema(canonical_schema)

    def _load_prompt(self, path: Path) -> str:
        """Load a nonblank, versioned prompt asset without interpolating mission data."""
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise MissionProviderError(f"Mission prompt is empty: {path}")
        return prompt

    def _satisfaction_policy_input(self) -> JSONObject | None:
        """Expose the startup-frozen system policy equally to planning, review, and repair."""
        policy = self._settings.satisfaction_policy
        return None if policy is None else policy.to_json()

    def _request(
        self,
        *,
        model: str,
        instructions: str,
        input_text: str,
        schema_name: str,
        schema: JSONObject,
    ) -> JSONObject:
        """Send one bounded, non-streaming Responses request with strict structured output."""
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload: JSONObject = {
            "model": model,
            "instructions": instructions,
            "input": input_text,
            "store": not self._settings.llm.disable_response_storage,
            "reasoning": {"effort": self._settings.llm.reasoning_effort},
            "max_output_tokens": self._settings.llm.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        response = self._transport.post_json(
            self._endpoint,
            headers,
            payload,
            self._settings.llm.timeout_seconds,
        )
        if response.get("error") is not None:
            raise MissionProviderError(f"provider returned an error: {response['error']}")
        if response.get("status") != "completed":
            raise MissionProviderError(
                f"provider response was not completed: {response.get('status')}"
            )
        return response

    def _extract_output_json(self, response: JSONObject) -> JSONObject:
        """Extract the first output_text JSON object from a completed Responses payload."""
        output = response.get("output")
        if not isinstance(output, list):
            raise MissionProviderError("provider response has no output array")
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text")
                if not isinstance(text, str):
                    continue
                decoded: object = json.loads(text)
                if isinstance(decoded, dict) and all(isinstance(key, str) for key in decoded):
                    return cast(JSONObject, decoded)
                raise MissionProviderError("provider output_text must decode to a JSON object")
        raise MissionProviderError("provider response contains no output_text")


class ResponsesMissionPlanner:
    """Create MissionPlan drafts through a Responses-compatible provider."""

    def __init__(
        self,
        settings: MissionSettings,
        environment: Mapping[str, str],
        transport: JsonTransport | None = None,
        execution_profile: DeploymentExecutionProfile | None = None,
    ) -> None:
        """Create a Planner over transport mechanics that carry no Mission authority."""
        self._client = _ResponsesClient(settings, environment, transport)
        self._settings = settings
        self._execution_profile = execution_profile

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Generate a strict MissionPlan from the complete resolved Mission intent."""
        return self._plan_attempt(
            mission_id,
            grounded_intent,
            capability_catalog,
            grounding_context,
            feedback=None,
        )

    def regenerate(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
        previous_provider_output: JSONObject,
        validation_errors: list[JSONObject],
    ) -> MissionPlan:
        """Regenerate one draft from the frozen inputs plus structured rejection feedback.

        This is the bounded pre-validation recovery port: it receives the raw
        rejected provider output (never an invalid MissionPlan object) and
        re-runs the full normalization and validation chain. It stays
        distinct from the post-validation Reviewer/Repairer, whose inputs
        are always already-valid MissionPlans.
        """
        return self._plan_attempt(
            mission_id,
            grounded_intent,
            capability_catalog,
            grounding_context,
            feedback=cast(
                JSONValue,
                {
                    "previous_rejected_provider_output": previous_provider_output,
                    "validation_errors": validation_errors,
                },
            ),
        )

    def _plan_attempt(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
        feedback: JSONValue | None,
    ) -> MissionPlan:
        """Run one provider attempt, attaching raw outputs to structure failures.

        Provider transport/identity failures stay ``MissionProviderError``
        and never carry draft payloads; only model-draft structure failures
        (``MissionPlanError``) are wrapped into ``RejectedPlanError`` with
        the raw and normalized outputs for evidence and recovery.
        """
        canonical_schema = self._client._load_schema()
        payload: dict[str, JSONValue] = {
            "mission_id": mission_id,
            "grounded_intent": grounded_intent.to_json(),
            "satisfaction_policy": self._client._satisfaction_policy_input(),
            "capability_catalog": capability_catalog.to_json(),
            "grounding_context": grounding_context.to_json(),
            **(
                {"deployment_execution_profile": self._execution_profile.to_json()}
                if self._execution_profile is not None
                else {}
            ),
        }
        if feedback is not None:
            payload["prevalidation_recovery_feedback"] = feedback
        response = self._client._request(
            model=self._settings.llm.model,
            instructions=self._client._load_prompt(self._settings.prompts.planner_path),
            input_text=json.dumps(
                _with_semantic_goal(payload, grounding_context),
                ensure_ascii=False,
                sort_keys=True,
            ),
            schema_name="mission_plan_v0",
            schema=self._client._mission_plan_provider_schema(canonical_schema),
        )
        generated_at_ms = int(time.time() * 1000)
        provider_output = self._client._extract_output_json(response)
        try:
            normalized = normalize_mission_plan_provider_output(provider_output, canonical_schema)
        except MissionPlanError as error:
            raise RejectedPlanError(
                str(error),
                stage="normalization",
                provider_output=provider_output,
                normalized_output=None,
                generated_at_ms=generated_at_ms,
            ) from error
        try:
            return _validate_plan_output(
                normalized,
                mission_id,
                grounded_intent,
                capability_catalog,
                self._settings.satisfaction_policy,
                grounding_context,
                self._execution_profile,
            )
        except MissionPlanError as error:
            raise RejectedPlanError(
                str(error),
                stage="plan_validation",
                provider_output=provider_output,
                normalized_output=normalized,
                generated_at_ms=generated_at_ms,
            ) from error


class ResponsesMissionReviewer:
    """Review validated MissionPlan drafts without modifying them."""

    def __init__(
        self,
        settings: MissionSettings,
        environment: Mapping[str, str],
        transport: JsonTransport | None = None,
        execution_profile: DeploymentExecutionProfile | None = None,
    ) -> None:
        """Create a Reviewer adapter over the shared Responses request implementation."""
        self._client = _ResponsesClient(settings, environment, transport)
        self._settings = settings
        self._execution_profile = execution_profile

    def review(
        self,
        grounded_intent: GroundedIntent,
        plan: MissionPlan,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlanReview:
        """Review the plan against its exact grounded input and authority boundaries."""
        validate_satisfaction_policy(plan, self._settings.satisfaction_policy)
        response = self._client._request(
            model=self._settings.llm.review_model,
            instructions=self._client._load_prompt(self._settings.prompts.reviewer_path),
            input_text=json.dumps(
                _with_semantic_goal(
                    {
                        "grounded_intent": grounded_intent.to_json(),
                        "mission_plan": plan.to_json(),
                        "satisfaction_policy": self._client._satisfaction_policy_input(),
                        "capability_catalog": capability_catalog.to_json(),
                        "grounding_context": grounding_context.to_json(),
                        **(
                            {"deployment_execution_profile": self._execution_profile.to_json()}
                            if self._execution_profile is not None
                            else {}
                        ),
                    },
                    grounding_context,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            schema_name="mission_review_v0",
            schema=_review_schema(),
        )
        return MissionPlanReview.from_json(self._client._extract_output_json(response))


class ResponsesMissionRepairer:
    """Repair one rejected MissionPlan without expanding grounded Mission facts."""

    def __init__(
        self,
        settings: MissionSettings,
        environment: Mapping[str, str],
        transport: JsonTransport | None = None,
        execution_profile: DeploymentExecutionProfile | None = None,
    ) -> None:
        """Create a Repairer adapter over the shared Responses request implementation."""
        self._client = _ResponsesClient(settings, environment, transport)
        self._settings = settings
        self._execution_profile = execution_profile

    def repair(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        rejected_plan: MissionPlan,
        review: MissionPlanReview,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Generate and validate one complete replacement draft from structured findings."""
        canonical_schema = self._client._load_schema()
        response = self._client._request(
            model=self._settings.llm.model,
            instructions=self._client._load_prompt(self._settings.prompts.repairer_path),
            input_text=json.dumps(
                _with_semantic_goal(
                    {
                        "mission_id": mission_id,
                        "grounded_intent": grounded_intent.to_json(),
                        "rejected_plan": rejected_plan.to_json(),
                        "satisfaction_policy": self._client._satisfaction_policy_input(),
                        "review": review.to_json(),
                        "capability_catalog": capability_catalog.to_json(),
                        "grounding_context": grounding_context.to_json(),
                        **(
                            {"deployment_execution_profile": self._execution_profile.to_json()}
                            if self._execution_profile is not None
                            else {}
                        ),
                    },
                    grounding_context,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            schema_name="mission_plan_repair_v0",
            schema=self._client._mission_plan_provider_schema(canonical_schema),
        )
        return _validate_plan_output(
            normalize_mission_plan_provider_output(
                self._client._extract_output_json(response), canonical_schema
            ),
            mission_id,
            grounded_intent,
            capability_catalog,
            self._settings.satisfaction_policy,
            grounding_context,
            self._execution_profile,
        )


class ResponsesMissionInterpreter:
    """Ground text instructions through the same bounded Responses provider boundary."""

    def __init__(
        self,
        settings: MissionSettings,
        environment: Mapping[str, str],
        transport: JsonTransport | None = None,
    ) -> None:
        """Create a provider adapter while reusing strict request and response handling."""
        self._client = _ResponsesClient(settings, environment, transport)
        self._settings = settings

    def interpret(
        self,
        dialogue: tuple[DialogueTurn, ...],
        grounding_context: GroundingContextSnapshot,
    ) -> IntentAssessment:
        """Return grounded intent or explicit questions without decomposing or executing Tasks."""
        schema: JSONObject = {
            "type": "object",
            "additionalProperties": False,
            "required": ["objective", "constraints", "assumptions", "open_questions"],
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "The resolved user goal without optional added work.",
                },
                "constraints": {
                    "type": "array",
                    "description": "Confirmed user limitations, not inferred deployment facts.",
                    "items": {"type": "string"},
                },
                "assumptions": {
                    "type": "array",
                    "description": (
                        "Visible non-blocking interpretations that preserve the core goal."
                    ),
                    "items": {"type": "string"},
                },
                "open_questions": {
                    "type": "array",
                    "description": (
                        "Only blocking unresolved questions for Mission semantic commitment; "
                        "return an empty array when none remain."
                    ),
                    "items": {"type": "string"},
                },
            },
        }
        response = self._client._request(
            model=self._settings.llm.model,
            instructions=self._client._load_prompt(self._settings.prompts.interpreter_path),
            input_text=json.dumps(
                {
                    "dialogue": [turn.to_json() for turn in dialogue],
                    "grounding_context": grounding_context.to_json(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            schema_name="mission_intent_v0",
            schema=schema,
        )
        return IntentAssessment.from_json(self._client._extract_output_json(response))
