"""Responses-compatible LLM adapter for structured Mission planning and review."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from mission.config import MissionSettings
from mission.controller import InventorySnapshot
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.requests import IntentAssessment


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


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Capture a model review decision without adding it to the Mission Plan contract."""

    approved: bool
    issues: tuple[str, ...]


class ResponsesMissionPlanner:
    """Plan and optionally review a Mission through a Responses-compatible provider."""

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

    def plan(self, mission_id: str, objective: str) -> MissionPlan:
        """Generate a strict MissionPlan v0 and reject plans that fail contract or review."""
        schema = self._load_schema()
        response = self._request(
            model=self._settings.llm.model,
            instructions=self._load_prompt(self._settings.prompts.planner_path),
            input_text=json.dumps(
                {"mission_id": mission_id, "objective": objective}, ensure_ascii=False
            ),
            schema_name="mission_plan_v0",
            schema=cast(JSONObject, self._provider_schema(schema)),
        )
        plan = MissionPlan.from_json(self._extract_output_json(response))
        if plan.mission.mission_id != mission_id:
            raise MissionProviderError("model changed the requested mission id")
        if plan.mission.objective != objective:
            raise MissionProviderError("model changed the requested mission objective")
        if self._settings.review_enabled:
            review = self._review(plan)
            if not review.approved:
                raise MissionProviderError(f"mission plan review rejected: {list(review.issues)}")
        return plan

    def _load_schema(self) -> JSONObject:
        """Load the configured JSON Schema used for strict provider output."""
        decoded: object = json.loads(self._settings.schema_path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise MissionProviderError("Mission Plan schema must be a JSON object")
        return cast(JSONObject, decoded)

    def _load_prompt(self, path: Path) -> str:
        """Load a nonblank, versioned prompt asset without interpolating mission data."""
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise MissionProviderError(f"Mission prompt is empty: {path}")
        return prompt

    def _provider_schema(self, value: JSONValue) -> JSONValue:
        """Project the full contract into the strict subset accepted by Responses providers."""
        if isinstance(value, list):
            return [self._provider_schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        unsupported = {"$schema", "$id", "title", "minLength", "pattern", "uniqueItems"}
        projected: JSONObject = {}
        for key, item in value.items():
            if key in unsupported:
                continue
            if key == "const":
                projected["enum"] = [self._provider_schema(item)]
                continue
            projected[key] = self._provider_schema(item)
        return projected

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

    def _review(self, plan: MissionPlan) -> ReviewResult:
        """Ask the configured review model to detect contract and authority-boundary defects."""
        review_schema: JSONObject = {
            "type": "object",
            "additionalProperties": False,
            "required": ["approved", "issues"],
            "properties": {
                "approved": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
        }
        response = self._request(
            model=self._settings.llm.review_model,
            instructions=self._load_prompt(self._settings.prompts.reviewer_path),
            input_text=json.dumps(plan.to_json(), ensure_ascii=False, sort_keys=True),
            schema_name="mission_review_v0",
            schema=review_schema,
        )
        decoded = self._extract_output_json(response)
        approved = decoded.get("approved")
        issues_value = decoded.get("issues")
        if not isinstance(approved, bool) or not isinstance(issues_value, list):
            raise MissionProviderError("review response does not match the review contract")
        if not all(isinstance(issue, str) for issue in issues_value):
            raise MissionProviderError("review issues must contain only text")
        return ReviewResult(approved=approved, issues=tuple(cast(list[str], issues_value)))


class ResponsesMissionInterpreter:
    """Ground text instructions through the same bounded Responses provider boundary."""

    def __init__(
        self,
        settings: MissionSettings,
        environment: Mapping[str, str],
        transport: JsonTransport | None = None,
    ) -> None:
        """Create a provider adapter while reusing strict request and response handling."""
        self._client = ResponsesMissionPlanner(settings, environment, transport)
        self._settings = settings

    def interpret(
        self,
        instruction: str,
        messages: tuple[str, ...],
        inventory: InventorySnapshot,
    ) -> IntentAssessment:
        """Return grounded intent or explicit questions without decomposing or executing Tasks."""
        schema: JSONObject = {
            "type": "object",
            "additionalProperties": False,
            "required": ["objective", "constraints", "assumptions", "open_questions"],
            "properties": {
                "objective": {"type": "string"},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
            },
        }
        response = self._client._request(
            model=self._settings.llm.model,
            instructions=self._client._load_prompt(self._settings.prompts.interpreter_path),
            input_text=json.dumps(
                {
                    "instruction": instruction,
                    "messages": list(messages),
                    "inventory": inventory.to_json(),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            schema_name="mission_intent_v0",
            schema=schema,
        )
        return IntentAssessment.from_json(self._client._extract_output_json(response))
