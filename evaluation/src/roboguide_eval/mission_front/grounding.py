"""Fixture-grounded mode for the Mission Front-half eval.

This module reuses the official ``GroundingContextSnapshot`` contract
(``mission.grounding_context``) directly — scenarios declare evidence in a
compact YAML form, and :class:`FixtureGroundingReader` builds the real
contract objects and binds each snapshot to the exact current dialogue
revision, mirroring what the production ``HttpMissionGroundingReader`` does.
No separate grounding semantics are invented: everything the model sees is a
genuine ``GroundingContextSnapshot`` produced by the contract constructors.

Two run modes exist:

- ``context_free`` — the engine's default ``EmptyMissionGroundingReader``:
  the deliberation runs without deployment evidence (the original baseline
  behavior);
- ``fixture_grounded`` — a :class:`FixtureGroundingReader` injects curated
  State/Memory evidence or gaps so canary scenarios can observe
  grounding-sensitive clarification behavior (fresh unique evidence,
  conflicts, stale evidence, metadata-only memory, source gaps).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml
from mission.grounding_context import (
    GroundingContextError,
    GroundingContextSnapshot,
    GroundingFreshness,
    GroundingGap,
    MemoryContentStatus,
    MemoryGroundingEvidence,
    StateGroundingEvidence,
    dialogue_digest,
    evidence_id,
)
from mission.request_record import DialogueTurn
from mission.responses import UrllibJsonTransport

from roboguide_eval.mission_front.cases import (
    CaseExpectations,
    MissionFrontCase,
    MissionFrontCaseError,
)
from roboguide_eval.mission_front.runner import run_case

GROUNDING_SCENARIOS_SCHEMA: Final = "roboguide-eval.mission-front-grounding/v0.1"
GROUNDING_MODES: Final = frozenset({"context_free", "fixture_grounded"})
FRESHNESS_VALUES: Final = frozenset(item.value for item in GroundingFreshness)
CONTENT_STATUS_VALUES: Final = frozenset(item.value for item in MemoryContentStatus)
DEFAULT_VALID_FOR_MS: Final = 3_600_000


class GroundingScenarioError(ValueError):
    """Report an invalid grounding scenario definition."""


@dataclass(frozen=True, slots=True)
class StateFixture:
    """Declare one World State evidence entry in scenario YAML form."""

    fields: dict[str, object]


@dataclass(frozen=True, slots=True)
class GroundingScenario:
    """Define one grounding A/B canary: instruction, mode, and fixture."""

    scenario_id: str
    pair_id: str
    mode: str
    instruction: str
    follow_ups: tuple[str, ...]
    state_entries: tuple[dict[str, object], ...]
    memory_entries: tuple[dict[str, object], ...]
    gap_entries: tuple[dict[str, object], ...]
    expectations: CaseExpectations


def load_grounding_scenarios(path: Path) -> tuple[GroundingScenario, ...]:
    """Load and validate one grounding canary scenario file.

    Args:
        path: YAML file with the versioned ``scenarios`` list.

    Returns:
        The validated scenarios in declaration order.

    Raises:
        GroundingScenarioError: If the file is unreadable, malformed, or any
            scenario violates the scenario contract.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise GroundingScenarioError(f"cannot read scenario file {path}: {error}") from error
    except yaml.YAMLError as error:
        raise GroundingScenarioError(f"scenario file {path} is not valid YAML: {error}") from error
    if not isinstance(document, dict) or not all(isinstance(key, str) for key in document):
        raise GroundingScenarioError(f"{path} must be a mapping")
    if document.get("schema") != GROUNDING_SCENARIOS_SCHEMA:
        raise GroundingScenarioError(
            f"{path} schema must be {GROUNDING_SCENARIOS_SCHEMA!r}, got {document.get('schema')!r}"
        )
    raw = document.get("scenarios")
    if not isinstance(raw, list) or not raw:
        raise GroundingScenarioError(f"{path}.scenarios must be a nonempty list")
    scenarios: list[GroundingScenario] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        scenario = _parse_scenario(item, f"{path}.scenarios[{index}]")
        if scenario.scenario_id in seen:
            raise GroundingScenarioError(f"{path}: duplicate scenario id {scenario.scenario_id!r}")
        seen.add(scenario.scenario_id)
        scenarios.append(scenario)
    return tuple(scenarios)


def _parse_scenario(item: object, path: str) -> GroundingScenario:
    """Parse one scenario mapping.

    Args:
        item: The decoded YAML mapping for one scenario.
        path: Dotted path for error messages.

    Returns:
        The validated :class:`GroundingScenario`.

    Raises:
        GroundingScenarioError: If required fields are missing or malformed.
    """
    if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
        raise GroundingScenarioError(f"{path} must be a mapping")
    allowed = {"id", "pair", "mode", "instruction", "follow_ups", "grounding", "expect"}
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise GroundingScenarioError(f"{path} has unknown keys: {unknown}")
    scenario_id = item.get("id")
    pair_id = item.get("pair")
    mode = item.get("mode")
    instruction = item.get("instruction")
    if not isinstance(scenario_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{2,63}", scenario_id
    ):
        raise GroundingScenarioError(f"{path}.id must match [a-z0-9][a-z0-9-]{{2,63}}")
    if not isinstance(pair_id, str) or not pair_id.strip():
        raise GroundingScenarioError(f"{path}.pair must be nonblank text")
    if not isinstance(mode, str) or mode not in GROUNDING_MODES:
        raise GroundingScenarioError(f"{path}.mode must be one of {sorted(GROUNDING_MODES)}")
    if not isinstance(instruction, str) or not instruction.strip():
        raise GroundingScenarioError(f"{path}.instruction must be nonblank text")
    follow_ups = item.get("follow_ups", [])
    if not isinstance(follow_ups, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in follow_ups
    ):
        raise GroundingScenarioError(f"{path}.follow_ups must be a list of nonblank strings")
    grounding = item.get("grounding", {})
    if grounding is None:
        grounding = {}
    if not isinstance(grounding, dict):
        raise GroundingScenarioError(f"{path}.grounding must be a mapping")
    state_entries: list[dict[str, object]] = _entry_list(grounding, "state", path)
    memory_entries: list[dict[str, object]] = _entry_list(grounding, "memory", path)
    gap_entries: list[dict[str, object]] = _entry_list(grounding, "gaps", path)
    if mode == "context_free" and (state_entries or memory_entries or gap_entries):
        raise GroundingScenarioError(f"{path}.grounding must be empty in context_free mode")
    expectations_value = item.get("expect", {})
    try:
        from roboguide_eval.mission_front.cases import _parse_expectations

        expectations = _parse_expectations(expectations_value, f"{path}.expect")
    except MissionFrontCaseError as error:
        raise GroundingScenarioError(str(error)) from error
    return GroundingScenario(
        scenario_id=scenario_id,
        pair_id=pair_id,
        mode=mode,
        instruction=instruction,
        follow_ups=tuple(follow_ups),
        state_entries=tuple(state_entries),
        memory_entries=tuple(memory_entries),
        gap_entries=tuple(gap_entries),
        expectations=expectations,
    )


def _entry_list(grounding: dict[str, object], key: str, path: str) -> list[dict[str, object]]:
    """Read one grounding entry list.

    Args:
        grounding: The grounding mapping.
        key: Entry list key (``state`` / ``memory`` / ``gaps``).
        path: Dotted path for error messages.

    Returns:
        The entry list (empty when absent).

    Raises:
        GroundingScenarioError: If the value is not a list of mappings.
    """
    entries = grounding.get(key, [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise GroundingScenarioError(f"{path}.grounding.{key} must be a list of mappings")
    return entries


def _require_entry_text(entry: dict[str, object], key: str, path: str) -> str:
    """Read one required nonblank text entry field.

    Args:
        entry: The scenario entry mapping.
        key: The field name.
        path: Dotted path for error messages.

    Returns:
        The text value.

    Raises:
        GroundingScenarioError: If the field is missing or blank.
    """
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GroundingScenarioError(f"{path}.{key} must be nonblank text")
    return value


def _entry_int(
    entry: dict[str, object],
    key: str,
    path: str,
    default: int | None = None,
    *,
    minimum: int = 0,
) -> int:
    """Read one integer entry field with an optional default.

    Args:
        entry: The scenario entry mapping.
        key: The field name.
        path: Dotted path for error messages.
        default: Value used when the key is absent.
        minimum: Inclusive lower bound.

    Returns:
        The integer value.

    Raises:
        GroundingScenarioError: If the value is not an integer within bounds.
    """
    value = entry.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GroundingScenarioError(f"{path}.{key} must be an integer >= {minimum}")
    return value


def _optional_entry_text(entry: dict[str, object], key: str, path: str) -> str | None:
    """Read one optional nonblank text entry field.

    Args:
        entry: The scenario entry mapping.
        key: The field name.
        path: Dotted path for error messages.

    Returns:
        The text value or ``None``.

    Raises:
        GroundingScenarioError: If the value is present but blank.
    """
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GroundingScenarioError(f"{path}.{key} must be nonblank text when present")
    return value


class FixtureGroundingReader:
    """Implement the official reader protocol with curated contract objects.

    The reader rebuilds a fresh, digest-correct snapshot for every capture
    call: the engine may re-capture after clarification answers changed the
    dialogue, and each snapshot must bind to that exact dialogue revision.
    """

    def __init__(
        self,
        *,
        state_entries: tuple[dict[str, object], ...] = (),
        memory_entries: tuple[dict[str, object], ...] = (),
        gap_entries: tuple[dict[str, object], ...] = (),
        label: str = "fixture",
    ) -> None:
        """Create the reader from scenario entry mappings.

        Args:
            state_entries: World State evidence entries.
            memory_entries: Global Memory manifest entries.
            gap_entries: Fail-soft acquisition gap entries.
            label: Reader label recorded in suite summaries.
        """
        self._state_entries = state_entries
        self._memory_entries = memory_entries
        self._gap_entries = gap_entries
        self.label = label

    def capture(
        self,
        request_id: str,
        dialogue: tuple[DialogueTurn, ...],
        captured_at_ms: int,
    ) -> GroundingContextSnapshot:
        """Build one official snapshot bound to the current dialogue revision.

        Args:
            request_id: The owning Mission Request identity.
            dialogue: The exact current dialogue; its digest is bound into
                the snapshot.
            captured_at_ms: The engine clock value for this capture.

        Returns:
            A contract-valid :class:`GroundingContextSnapshot`.

        Raises:
            GroundingScenarioError: If a fixture entry violates the official
                contract (the contract constructors raise first).
        """
        path = f"grounding[{self.label}]"
        try:
            state_evidence = tuple(
                self._build_state(entry, f"{path}.state[{index}]")
                for index, entry in enumerate(self._state_entries)
            )
            memory_evidence = tuple(
                self._build_memory(entry, f"{path}.memory[{index}]")
                for index, entry in enumerate(self._memory_entries)
            )
        except GroundingContextError as error:
            raise GroundingScenarioError(
                f"fixture entry violates the official grounding contract: {error}"
            ) from error
        gaps = tuple(
            GroundingGap(
                code=_require_entry_text(entry, "code", f"{path}.gaps[{index}]"),
                source=_require_entry_text(entry, "source", f"{path}.gaps[{index}]"),
                detail=_require_entry_text(entry, "detail", f"{path}.gaps[{index}]"),
            )
            for index, entry in enumerate(self._gap_entries)
        )
        return GroundingContextSnapshot.create(
            request_id=request_id,
            dialogue_digest=dialogue_digest(tuple(turn.to_json() for turn in dialogue)),
            captured_at_ms=captured_at_ms,
            state_evidence=state_evidence,
            memory_evidence=memory_evidence,
            gaps=gaps,
            selection_policy_ref=f"roboguide-eval.fixture-grounding/{self.label}",
        )

    def _build_state(self, entry: dict[str, object], path: str) -> StateGroundingEvidence:
        """Build one contract ``StateGroundingEvidence`` from a scenario entry.

        Args:
            entry: The scenario state entry.
            path: Dotted path for error messages.

        Returns:
            The contract-valid evidence object (identity auto-derived from
            content when the fixture omits ``evidence_id``).
        """
        freshness_value = _require_entry_text(entry, "freshness", path)
        if freshness_value not in FRESHNESS_VALUES:
            raise GroundingScenarioError(
                f"{path}.freshness must be one of {sorted(FRESHNESS_VALUES)}"
            )
        payload_schema = _require_entry_text(entry, "payload_schema", path)
        received_at_ms = _entry_int(entry, "received_at_ms", path, 0)
        valid_for_ms = _entry_int(entry, "valid_for_ms", path, DEFAULT_VALID_FOR_MS, minimum=1)

        def build(evidence_id_value: str) -> StateGroundingEvidence:
            """Build one evidence object with the given identity.

            Args:
                evidence_id_value: The explicit or derived evidence identity.

            Returns:
                The contract-valid evidence object.
            """
            return StateGroundingEvidence(
                evidence_id=evidence_id_value,
                object_type=_require_entry_text(entry, "object_type", path),
                object_id=_require_entry_text(entry, "object_id", path),
                semantic=_require_entry_text(entry, "semantic", path),
                source=_require_entry_text(entry, "source", path),
                channel_id=_require_entry_text(entry, "channel_id", path),
                payload_schema=payload_schema,
                value=_entry_object(entry, "value", path),
                source_observed_at_ms=_entry_int(
                    entry, "source_observed_at_ms", path, received_at_ms
                ),
                received_at_ms=received_at_ms,
                valid_for_ms=valid_for_ms,
                freshness=GroundingFreshness(freshness_value),
                confidence_millionths=entry.get("confidence_millionths"),
                source_epoch=_optional_entry_text(entry, "source_epoch", path),
                sequence=_entry_int(entry, "sequence", path, 1),
            )

        explicit_id = _optional_entry_text(entry, "evidence_id", path)
        if explicit_id:
            return build(explicit_id)
        return build(evidence_id("state:", build("pending").to_json()))

    def _build_memory(self, entry: dict[str, object], path: str) -> MemoryGroundingEvidence:
        """Build one contract ``MemoryGroundingEvidence`` from a scenario entry.

        Args:
            entry: The scenario memory entry.
            path: Dotted path for error messages.

        Returns:
            The contract-valid evidence object.

        Raises:
            GroundingScenarioError: If a declared enum value is unsupported.
        """
        kind = _require_entry_text(entry, "kind", path)
        visibility = _require_entry_text(entry, "visibility", path)
        content_status = entry.get("content_status", MemoryContentStatus.METADATA_ONLY.value)
        if content_status not in CONTENT_STATUS_VALUES:
            raise GroundingScenarioError(
                f"{path}.content_status must be one of {sorted(CONTENT_STATUS_VALUES)}"
            )
        owner = entry.get("owner")
        owner_object = owner if isinstance(owner, dict) else {}

        def build(evidence_id_value: str) -> MemoryGroundingEvidence:
            """Build one evidence object with the given identity.

            Args:
                evidence_id_value: The explicit or derived evidence identity.

            Returns:
                The contract-valid evidence object.
            """
            return MemoryGroundingEvidence(
                evidence_id=evidence_id_value,
                memory_id=_require_entry_text(entry, "memory_id", path),
                revision_id=_require_entry_text(entry, "revision_id", path),
                kind=kind,
                provider_id=_require_entry_text(entry, "provider_id", path),
                owner=owner_object,
                scope=_optional_entry_text(entry, "scope", path) or "global",
                visibility=visibility,
                payload_schema=_require_entry_text(entry, "payload_schema", path),
                media_type=_optional_entry_text(entry, "media_type", path)
                or "application/octet-stream",
                artifact=entry.get("artifact") if isinstance(entry.get("artifact"), dict) else None,
                source_mission_id=_optional_entry_text(entry, "source_mission_id", path),
                source_execution_id=_optional_entry_text(entry, "source_execution_id", path),
                source_task_ref=(
                    entry.get("source_task_ref")
                    if isinstance(entry.get("source_task_ref"), dict)
                    else None
                ),
                created_at_ms=_entry_int(entry, "created_at_ms", path, 0),
                content_status=MemoryContentStatus(content_status),
            )

        explicit_id = _optional_entry_text(entry, "evidence_id", path)
        if explicit_id:
            return build(explicit_id)
        return build(evidence_id("memory:", build("pending").to_json()))


def _entry_object(entry: dict[str, object], key: str, path: str) -> dict[str, object]:
    """Read one required mapping entry field.

    Args:
        entry: The scenario entry mapping.
        key: The field name.
        path: Dotted path for error messages.

    Returns:
        The mapping value.

    Raises:
        GroundingScenarioError: If the value is not a mapping.
    """
    value = entry.get(key)
    if not isinstance(value, dict):
        raise GroundingScenarioError(f"{path}.{key} must be a mapping")
    return value


def scenario_to_case(scenario: GroundingScenario) -> MissionFrontCase:
    """Project one grounding scenario onto the plain case contract.

    Args:
        scenario: The grounding scenario.

    Returns:
        A :class:`MissionFrontCase` carrying the same instruction, follow-ups,
        and expectations, so the existing invariant machinery applies as-is.
    """
    return MissionFrontCase(
        case_id=scenario.scenario_id,
        category="grounding",
        instruction=scenario.instruction,
        follow_ups=scenario.follow_ups,
        expectations=scenario.expectations,
    )


def build_scenario_reader(scenario: GroundingScenario) -> FixtureGroundingReader | None:
    """Build the reader for one scenario according to its mode.

    Args:
        scenario: The grounding scenario.

    Returns:
        A :class:`FixtureGroundingReader` for ``fixture_grounded`` mode,
        ``None`` for ``context_free`` mode (engine default empty context).
    """
    if scenario.mode == "context_free":
        return None
    return FixtureGroundingReader(
        state_entries=scenario.state_entries,
        memory_entries=scenario.memory_entries,
        gap_entries=scenario.gap_entries,
        label=f"fixture:{scenario.scenario_id}",
    )


def run_grounding_suite(
    scenarios: tuple[GroundingScenario, ...],
    *,
    repository_root: Path,
    out_dir: Path,
    only: tuple[str, ...] = (),
    llm_timeout_override: float | None = None,
) -> dict[str, object]:
    """Run grounding A/B canaries: one fresh engine per scenario.

    Each scenario gets its own suite assembly (own store, transport, and
    reader) so runs never share request state and every context_free /
    fixture_grounded pair is attributable. Results reuse the case runner and
    invariant machinery; the summary adds a per-pair A/B comparison table.

    Args:
        scenarios: The loaded grounding scenarios.
        repository_root: Repository root for configuration resolution.
        out_dir: Output directory receiving suite evidence.
        only: Optional filter matching scenario ids or pair ids (repeatable);
            both arms of a matched pair run so A/B comparison stays intact.
        llm_timeout_override: Optional diagnostic timeout ceiling (seconds)
            applied in-memory only; production config stays untouched.

    Returns:
        The summary document with per-pair comparison written to
        ``summary.json``.

    Raises:
        Exception: Configuration/provider assembly errors propagate — the
            suite cannot start without a usable pipeline.
    """
    if only:
        selected = tuple(
            scenario
            for scenario in scenarios
            if scenario.scenario_id in only or scenario.pair_id in only
        )
    else:
        selected = scenarios
    from roboguide_eval.mission_front.recording import RecordingTransport, StageScope
    from roboguide_eval.mission_front.runner import (
        _usage_totals,
        build_suite_components,
        new_suite_id,
        result_summary_json,
    )

    suite_id = new_suite_id()
    run_dir = out_dir / suite_id
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    pairs: dict[str, dict[str, object]] = {}
    for scenario in selected:
        stages = StageScope()
        transport = RecordingTransport(UrllibJsonTransport(), stages)
        suite = build_suite_components(
            repository_root,
            transport,
            stages,
            grounding_reader=build_scenario_reader(scenario),
            llm_timeout_override=llm_timeout_override,
        )
        eval_case = scenario_to_case(scenario)
        result = run_case(eval_case, suite, 0)
        (cases_dir / f"{scenario.scenario_id}.json").write_text(
            json.dumps(result_summary_json(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary_line = result.summary_line()
        summary_line["mode"] = scenario.mode
        summary_line["pair"] = scenario.pair_id
        summaries.append(summary_line)
        pair: dict[str, object] = pairs.setdefault(
            scenario.pair_id,
            {"instruction": scenario.instruction, "modes": {}},
        )
        raw_modes = pair["modes"]
        modes: dict[str, object] = raw_modes if isinstance(raw_modes, dict) else {}
        dialogue = result.record.get("dialogue")
        turns = dialogue if isinstance(dialogue, list) else []
        modes[scenario.mode] = {
            "final_lifecycle": result.final_lifecycle,
            "clarification_questions": sum(
                1
                for turn in turns
                if isinstance(turn, dict) and turn.get("kind") == "ClarificationQuestion"
            ),
            "llm_calls": len(result.llm_calls),
            "token_totals": _usage_totals(result.llm_calls),
            "failed_invariants": [
                outcome.name for outcome in result.invariants if outcome.passed is False
            ],
        }
        pair["modes"] = modes
    summary = {
        "suite_id": suite_id,
        "scenarios_executed": len(selected),
        "scenarios_passed": sum(1 for entry in summaries if entry["passed"]),
        "pairs": dict(sorted(pairs.items())),
        "scenarios": summaries,
    }
    (run_dir / "cases.jsonl").write_text(
        chr(10).join(json.dumps(entry, ensure_ascii=False) for entry in summaries)
        + (chr(10) if summaries else ""),
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
