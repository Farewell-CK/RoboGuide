"""Regression tests for the Mission command-line contract."""

import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.config import load_settings
from mission.contract_values import MissionPlanError
from mission.grounding_context import dialogue_digest
from mission.models import JSONObject, MissionPlan
from mission.responses import UrllibJsonTransport
from mission.review import MissionReviewError

from mission import cli


def test_validate_defaults_to_current_capability_catalog() -> None:
    """Keep CLI validation aligned with the current canonical catalog version."""
    arguments = cli._parser().parse_args(["validate", "--input", "mission.json"])

    assert arguments.catalog == Path("contracts/capability/v0.3/catalog.json")


@pytest.mark.parametrize("case", ["approved", "needs-clarification", "identity-conflict"])
def test_review_draft_calls_only_reviewer_with_exact_source(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assemble missing host fields and publish only after one independent approval."""
    fixture = Path("scenarios/mission-front-half-v0.7/mission-plan.json")
    raw = cast(JSONObject, json.loads(fixture.read_text(encoding="utf-8")))
    expected = MissionPlan.from_json(raw)
    mission = cast(JSONObject, raw["mission"])
    mission.pop("objective")
    mission.pop("id")
    if case == "identity-conflict":
        mission["id"] = "wrong-mission-id"
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(raw), encoding="utf-8")
    output = tmp_path / "output.json"
    source = "  Arrange the aid kit delivery and verify arrival.\n"
    settings = load_settings(Path("config/mission.toml"))
    settings = replace(settings, provider=replace(settings.provider, base_url="http://localhost"))
    monkeypatch.setattr(cli, "load_settings", lambda *args, **kwargs: settings)
    monkeypatch.setattr(cli, "current_environment", lambda: {settings.provider.api_key_env: "test"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mission",
            "review-draft",
            "--input",
            str(draft),
            "--output",
            str(output),
            "--mission-id",
            expected.mission.mission_id,
            "--objective",
            expected.mission.objective,
            "--instruction",
            source,
            "--constraint",
            "Keep the verification Task.",
        ],
    )
    requests: list[JSONObject] = []

    def review(
        _transport: UrllibJsonTransport,
        _url: str,
        _headers: Mapping[str, str],
        payload: JSONObject,
        _timeout_seconds: float,
    ) -> JSONObject:
        """Record actual adapter output and return the scripted Review result."""
        requests.append(payload)
        issues = (
            []
            if case == "approved"
            else [
                {
                    "code": "source.scope_unclear",
                    "path": "/grounded_intent",
                    "message": "Which delivery is intended?",
                    "required_action": "RequestClarification",
                }
            ]
        )
        decision = json.dumps({"approved": case == "approved", "issues": issues})
        return {
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": decision}]}],
        }

    monkeypatch.setattr(UrllibJsonTransport, "post_json", review)
    if case == "identity-conflict":
        with pytest.raises(MissionPlanError, match="differs from --mission-id"):
            cli.main()
        assert not requests
        assert not output.exists()
        return
    if case == "approved":
        assert cli.main() == 0
        assert json.loads(output.read_text(encoding="utf-8")) == expected.to_json()
    else:
        with pytest.raises(MissionReviewError, match="Clarification.*source.scope_unclear"):
            cli.main()
        assert not output.exists()
    (request,) = requests
    assert request["model"] == settings.llm.review_model
    assert cast(dict[str, JSONObject], request["text"])["format"]["name"] == "mission_review_v0"
    review_input = json.loads(cast(str, request["input"]))
    assert review_input["mission_plan"] == expected.to_json()
    assert review_input["grounded_intent"]["constraints"] == ["Keep the verification Task."]
    assert review_input["original_dialogue"][0]["content"] == source
    assert review_input["grounding_context"]["dialogue_digest"] == dialogue_digest(
        tuple(review_input["original_dialogue"])
    )
