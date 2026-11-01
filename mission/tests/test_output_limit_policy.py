"""Assert that every production MI role omits all output-token limit parameters."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from mission.config import load_settings
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.responses import (
    ResponsesMissionInterpreter,
    ResponsesMissionPlanner,
    ResponsesMissionRepairer,
    ResponsesMissionReviewer,
)
from mission.review import MissionPlanReview
from test_planners import (
    FakeTransport,
    _current_catalog,
    _grounding,
    _local_settings,
    _provider_plan,
    _response,
    _review_output,
    _v0_8_plan,
)

FORBIDDEN = {"max_token", "max_tokens", "max_completion_tokens", "max_output_tokens"}


@pytest.mark.parametrize("role", ["interpreter", "planner", "reviewer", "repairer"])
def test_every_role_omits_token_limits(role: str) -> None:
    """Real adapters must parse a valid result without supplying token cap aliases."""
    raw = _v0_8_plan()
    plan = MissionPlan.from_json(raw)
    mission = cast(JSONObject, raw["mission"])
    intent = GroundedIntent(cast(str, mission["objective"]), (), ())
    response = (
        {"objective": intent.objective, "constraints": [], "assumptions": [], "open_questions": []}
        if role == "interpreter"
        else _review_output()
        if role == "reviewer"
        else _provider_plan(raw)
    )
    transport = FakeTransport([_response(cast(JSONObject, response))])
    settings, environment = _local_settings(), {"OPENAI_API_KEY": "test-only-key"}
    grounding, catalog = _grounding(), _current_catalog()
    if role == "interpreter":
        ResponsesMissionInterpreter(settings, environment, transport).interpret(
            (
                DialogueTurn(
                    "turn-1",
                    DialogueSpeaker.USER,
                    DialogueTurnKind.INSTRUCTION,
                    intent.objective,
                    1,
                ),
            ),
            grounding,
        )
    elif role == "planner":
        ResponsesMissionPlanner(settings, environment, transport).plan(
            cast(str, mission["id"]),
            intent,
            catalog,
            grounding,
        )
    elif role == "reviewer":
        ResponsesMissionReviewer(settings, environment, transport).review(
            intent, plan, catalog, grounding
        )
    else:
        ResponsesMissionRepairer(settings, environment, transport).repair(
            cast(str, mission["id"]),
            intent,
            plan,
            MissionPlanReview.from_json(_review_output()),
            catalog,
            grounding,
        )
    assert len(transport.requests) == 1
    payload = transport.requests[0][2]
    assert not FORBIDDEN & payload.keys()
    assert payload["reasoning"] == {"effort": settings.llm.reasoning_effort}
    assert cast(JSONObject, cast(JSONObject, payload["text"])["format"])["strict"] is True


@pytest.mark.parametrize("legacy_name", sorted(FORBIDDEN))
def test_legacy_config_cannot_reintroduce_output_limit(tmp_path: Path, legacy_name: str) -> None:
    """A legacy local TOML setting never reaches the active LLM configuration."""
    source = Path("config/mission.toml").read_text()
    config = tmp_path / "legacy.toml"
    config.write_text(source.replace("[mission.llm]", f"[mission.llm]\n{legacy_name} = 16384"))
    settings = load_settings(config, repository_root=Path.cwd())
    assert not any(hasattr(settings.llm, name) for name in FORBIDDEN)
