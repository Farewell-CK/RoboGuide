"""ExperimentSpec loading, configuration digesting, and machine-specific overrides.

The split of responsibilities is deliberate:

- The committed experiment spec (YAML) is portable. It describes the
  experiment and never contains API keys, Conda environment names, or
  machine-specific absolute paths.
- Local configuration (``evaluation/local.yaml``, Git-ignored, path
  overridable via ``ROBOGUIDE_EVAL_CONFIG``) supplies the machine-specific
  launch facts per system: working directory, executable, arguments, Conda
  environment, credential environment variables, and probe commands.
- Environment variables ``ROBOGUIDE_EVAL_<SYSTEM>_WORKDIR``,
  ``ROBOGUIDE_EVAL_<SYSTEM>_EXECUTABLE``, and
  ``ROBOGUIDE_EVAL_<SYSTEM>_CONDA_ENV`` override the local config file so a
  machine can be re-pointed without editing any file.

Resolution merges these layers (spec defaults < local config < environment
variables) into a validated :class:`~roboguide_eval.process.ProcessSpec` or
fails closed with the exact missing fields.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from roboguide_eval.metrics import validate_metric_names
from roboguide_eval.models import (
    EXPERIMENT_ID_PATTERN,
    EXPERIMENT_SPEC_VERSION,
    NAME_PATTERN,
    DatasetSpec,
    EnvironmentSpec,
    ExperimentSpec,
    ExperimentSpecError,
    LlmSpec,
    experiment_spec_to_json,
    is_safe_run_relative_path,
)
from roboguide_eval.process import ProcessSpec, conda_run_prefix

LOCAL_CONFIG_SCHEMA: Final = "roboguide-eval.local-config/v0.1"
LOCAL_CONFIG_ENVIRONMENT_VARIABLE: Final = "ROBOGUIDE_EVAL_CONFIG"
DEFAULT_LOCAL_CONFIG_PATH: Final = Path("evaluation/local.yaml")

type SpecTable = dict[str, object]


class EvaluationConfigError(ValueError):
    """Report an invalid local configuration or unresolvable environment."""


@dataclass(frozen=True, slots=True)
class EnvironmentOverrides:
    """Machine-specific launch facts for one system under test.

    Every field is optional; resolution fails closed for fields a system
    genuinely needs but no configuration layer provided.
    """

    working_directory: str | None = None
    executable: str | None = None
    arguments: tuple[str, ...] | None = None
    conda_environment: str | None = None
    conda_command: str | None = None
    environment_variables: Mapping[str, str] | None = None
    readiness_command: tuple[str, ...] | None = None
    version_probe_command: tuple[str, ...] | None = None


def _table(value: object, path: str) -> SpecTable:
    """Return a string-keyed mapping or reject the value with its path.

    Args:
        value: Decoded YAML value.
        path: Dotted configuration path for error messages.

    Returns:
        The mapping typed as a string-keyed object table.

    Raises:
        ExperimentSpecError: If the value is not a string-keyed mapping.
    """
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ExperimentSpecError(f"{path} must be a mapping")
    return value


def _required_keys(table: SpecTable, required: set[str], optional: set[str], path: str) -> None:
    """Reject missing required keys and unknown keys in one mapping.

    Args:
        table: The mapping to validate.
        required: Keys that must be present.
        optional: Additional keys that may be present.
        path: Dotted configuration path for error messages.

    Raises:
        ExperimentSpecError: If keys are missing or unknown.
    """
    actual = set(table)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing or unknown:
        raise ExperimentSpecError(f"{path} keys mismatch: missing={missing}, unknown={unknown}")


def _text(table: SpecTable, key: str, path: str) -> str:
    """Read required nonblank text from one mapping.

    Args:
        table: Source mapping.
        key: Key to read.
        path: Dotted configuration path for error messages.

    Returns:
        The nonblank text value.

    Raises:
        ExperimentSpecError: If the value is not nonblank text.
    """
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExperimentSpecError(f"{path}.{key} must be nonblank text")
    return value


def _optional_text(table: SpecTable, key: str, path: str) -> str | None:
    """Read optional nonblank text from one mapping.

    Args:
        table: Source mapping.
        key: Key to read.
        path: Dotted configuration path for error messages.

    Returns:
        The text value or ``None`` when the key is absent or explicitly null.

    Raises:
        ExperimentSpecError: If the value is present but not nonblank text.
    """
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ExperimentSpecError(f"{path}.{key} must be nonblank text when present")
    return value


def _text_list(table: SpecTable, key: str, path: str) -> tuple[str, ...]:
    """Read a list of nonblank strings from one mapping.

    Args:
        table: Source mapping.
        key: Key to read.
        path: Dotted configuration path for error messages.

    Returns:
        The values as a tuple of strings.

    Raises:
        ExperimentSpecError: If the value is not a list of nonblank strings.
    """
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(entry, str) and entry for entry in value):
        raise ExperimentSpecError(f"{path}.{key} must be a list of nonblank strings")
    return tuple(value)


def _positive_float(table: SpecTable, key: str, path: str) -> float:
    """Read a positive number from one mapping.

    Args:
        table: Source mapping.
        key: Key to read.
        path: Dotted configuration path for error messages.

    Returns:
        The positive numeric value.

    Raises:
        ExperimentSpecError: If the value is not a positive finite number.
    """
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ExperimentSpecError(f"{path}.{key} must be a positive number")
    return float(value)


def _optional_positive_float(table: SpecTable, key: str, path: str) -> float | None:
    """Read an optional positive number from one mapping.

    Args:
        table: Source mapping.
        key: Key to read.
        path: Dotted configuration path for error messages.

    Returns:
        The positive numeric value or ``None``.

    Raises:
        ExperimentSpecError: If the value is present but not positive.
    """
    if table.get(key) is None:
        return None
    return _positive_float(table, key, path)


def _string_mapping(table: SpecTable, key: str, path: str) -> dict[str, str]:
    """Read a string-to-string mapping from one mapping.

    Args:
        table: Source mapping.
        key: Key to read.
        path: Dotted configuration path for error messages.

    Returns:
        The mapping with string keys and string values.

    Raises:
        ExperimentSpecError: If the value is not a string-keyed mapping of
            string values.
    """
    value = table.get(key)
    if not isinstance(value, dict) or not all(
        isinstance(inner_key, str) and isinstance(inner_value, str)
        for inner_key, inner_value in value.items()
    ):
        raise ExperimentSpecError(f"{path}.{key} must be a mapping of strings to strings")
    return dict(value)


def _parse_llm(table: SpecTable, path: str) -> LlmSpec:
    """Parse the ``llm`` section of an experiment spec.

    Args:
        table: The top-level spec table.
        path: Dotted configuration path for error messages.

    Returns:
        The validated :class:`LlmSpec`.

    Raises:
        ExperimentSpecError: If required fields are missing or malformed.
    """
    section = _table(table.get("llm"), f"{path}.llm")
    _required_keys(
        section, {"provider", "model"}, {"reasoning_effort", "reasoning_options"}, f"{path}.llm"
    )
    reasoning_options = (
        _string_mapping(section, "reasoning_options", f"{path}.llm")
        if ("reasoning_options" in section)
        else None
    )
    return LlmSpec(
        provider=_text(section, "provider", f"{path}.llm"),
        model=_text(section, "model", f"{path}.llm"),
        reasoning_effort=_optional_text(section, "reasoning_effort", f"{path}.llm"),
        reasoning_options=reasoning_options,
    )


def _parse_dataset(table: SpecTable, path: str) -> DatasetSpec:
    """Parse the optional ``dataset`` section of an experiment spec.

    Args:
        table: The top-level spec table.
        path: Dotted configuration path for error messages.

    Returns:
        The validated :class:`DatasetSpec`, empty when the section is absent.
    """
    if "dataset" not in table or table.get("dataset") is None:
        return DatasetSpec()
    section = _table(table.get("dataset"), f"{path}.dataset")
    _required_keys(section, set(), {"name", "revision", "digest"}, f"{path}.dataset")
    return DatasetSpec(
        name=_optional_text(section, "name", f"{path}.dataset"),
        revision=_optional_text(section, "revision", f"{path}.dataset"),
        digest=_optional_text(section, "digest", f"{path}.dataset"),
    )


def _parse_environment(name: str, value: object, path: str) -> EnvironmentSpec:
    """Parse one entry of the ``environments`` section.

    Args:
        name: The system name this environment entry belongs to.
        value: The decoded environment mapping.
        path: Dotted configuration path for error messages.

    Returns:
        The validated :class:`EnvironmentSpec`.

    Raises:
        ExperimentSpecError: If the entry is malformed or declares a path
            that escapes the run directory or targets a harness-owned file.
    """
    section = _table(value, f"{path}.environments.{name}")
    environment_path = f"{path}.environments.{name}"
    _required_keys(
        section,
        set(),
        {"timeout_seconds", "metrics_source_path", "expected_output_paths"},
        environment_path,
    )
    metrics_source_path = _optional_text(section, "metrics_source_path", environment_path)
    if metrics_source_path is not None and not is_safe_run_relative_path(metrics_source_path):
        raise ExperimentSpecError(
            f"{environment_path}.metrics_source_path must stay inside the run directory and "
            f"must not target harness-owned files: {metrics_source_path!r}"
        )
    expected_output_paths = _text_list(section, "expected_output_paths", environment_path)
    for output_path in expected_output_paths:
        if not is_safe_run_relative_path(output_path):
            raise ExperimentSpecError(
                f"{environment_path}.expected_output_paths entries must stay inside the run "
                f"directory and must not target harness-owned files: {output_path!r}"
            )
    return EnvironmentSpec(
        name=name,
        timeout_seconds=_optional_positive_float(section, "timeout_seconds", environment_path),
        metrics_source_path=metrics_source_path,
        expected_output_paths=expected_output_paths,
    )


def load_experiment_spec(path: Path) -> ExperimentSpec:
    """Load and fully validate one experiment specification YAML file.

    Args:
        path: Path to the versioned experiment spec.

    Returns:
        The validated :class:`ExperimentSpec`.

    Raises:
        ExperimentSpecError: If the file cannot be read, is not a mapping,
            uses a wrong schema version, contains unknown or missing keys, or
            violates any field invariant.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ExperimentSpecError(f"cannot read experiment spec {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ExperimentSpecError(f"experiment spec {path} is not valid YAML: {error}") from error
    table = _table(document, str(path))
    _required_keys(
        table,
        {
            "schema",
            "experiment_id",
            "benchmark",
            "task",
            "episodes",
            "systems",
            "llm",
            "seeds",
            "metrics",
            "timeout_seconds",
        },
        {"description", "dataset", "context", "environments"},
        str(path),
    )
    schema = _text(table, "schema", str(path))
    if schema != EXPERIMENT_SPEC_VERSION:
        raise ExperimentSpecError(
            f"unsupported experiment spec schema {schema!r}; expected {EXPERIMENT_SPEC_VERSION!r}"
        )
    experiment_id = _text(table, "experiment_id", str(path))
    if not EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        raise ExperimentSpecError(f"experiment_id must match {EXPERIMENT_ID_PATTERN.pattern!r}")
    benchmark = _text(table, "benchmark", str(path))
    if not NAME_PATTERN.fullmatch(benchmark):
        raise ExperimentSpecError(f"benchmark must match {NAME_PATTERN.pattern!r}")
    task = _text(table, "task", str(path))
    if not NAME_PATTERN.fullmatch(task):
        raise ExperimentSpecError(f"task must match {NAME_PATTERN.pattern!r}")
    episodes = _text_list(table, "episodes", str(path))
    if not episodes:
        raise ExperimentSpecError("episodes must select at least one episode")
    if len(set(episodes)) != len(episodes):
        raise ExperimentSpecError("episodes must not contain duplicates")
    systems = _text_list(table, "systems", str(path))
    if not systems:
        raise ExperimentSpecError("systems must declare at least one system under test")
    if len(set(systems)) != len(systems):
        raise ExperimentSpecError("systems must not contain duplicates")
    for system in systems:
        if not NAME_PATTERN.fullmatch(system):
            raise ExperimentSpecError(f"system names must match {NAME_PATTERN.pattern!r}")
    seeds_value = table.get("seeds")
    if not isinstance(seeds_value, list) or not all(
        isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0 for seed in seeds_value
    ):
        raise ExperimentSpecError("seeds must be a list of nonnegative integers")
    if not seeds_value:
        raise ExperimentSpecError("seeds must declare at least one seed")
    metrics = _text_list(table, "metrics", str(path))
    if not metrics:
        raise ExperimentSpecError("metrics must declare at least one canonical metric")
    validate_metric_names(metrics)
    environments_table = _table(table.get("environments", {}), f"{str(path)}.environments")
    for system in environments_table:
        if system not in systems:
            raise ExperimentSpecError(
                f"environments entry {system!r} is not a declared system under test"
            )
    environments = {
        name: _parse_environment(name, value, str(path))
        for name, value in environments_table.items()
    }
    description = table.get("description", "")
    if not isinstance(description, str):
        raise ExperimentSpecError("description must be text when present")
    return ExperimentSpec(
        experiment_id=experiment_id,
        description=description,
        benchmark=benchmark,
        task=task,
        dataset=_parse_dataset(table, str(path)),
        context=_string_mapping(table, "context", str(path)) if "context" in table else {},
        episodes=episodes,
        systems=systems,
        llm=_parse_llm(table, str(path)),
        seeds=tuple(seeds_value),
        metrics=tuple(metrics),
        timeout_seconds=_positive_float(table, "timeout_seconds", str(path)),
        environments=environments,
    )


def spec_digest(spec: ExperimentSpec) -> str:
    """Return the stable SHA-256 digest of an experiment specification.

    The digest is computed over the canonical JSON serialization, so it is
    independent of YAML key order and never includes machine-specific data.

    Args:
        spec: The validated experiment specification.

    Returns:
        The lowercase hexadecimal SHA-256 digest.
    """
    canonical = json.dumps(
        experiment_spec_to_json(spec), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode(encoding="utf-8")).hexdigest()


def _parse_overrides(table: SpecTable, path: str) -> EnvironmentOverrides:
    """Parse one system's override table from local configuration.

    Args:
        table: The decoded override mapping for one system.
        path: Dotted configuration path for error messages.

    Returns:
        The validated :class:`EnvironmentOverrides`.

    Raises:
        EvaluationConfigError: If any field has the wrong type.
    """
    allowed = {
        "working_directory",
        "executable",
        "arguments",
        "conda_environment",
        "conda_command",
        "environment_variables",
        "readiness_command",
        "version_probe_command",
    }
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise EvaluationConfigError(f"{path} has unknown keys: {unknown}")

    def text(key: str) -> str | None:
        """Read one optional nonblank override string.

        Args:
            key: Override key to read.

        Returns:
            The value or ``None``.

        Raises:
            EvaluationConfigError: If the value is present but not text.
        """
        return _optional_text(table, key, path)

    def argv_list(key: str) -> tuple[str, ...] | None:
        """Read one optional argv override list.

        Args:
            key: Override key to read.

        Returns:
            The argv tuple or ``None``.

        Raises:
            EvaluationConfigError: If the value is present but malformed.
        """
        return _text_list(table, key, path) if key in table else None

    variables: Mapping[str, str] | None = None
    if table.get("environment_variables") is not None:
        variables = _string_mapping(table, "environment_variables", path)
    return EnvironmentOverrides(
        working_directory=text("working_directory"),
        executable=text("executable"),
        arguments=argv_list("arguments"),
        conda_environment=text("conda_environment"),
        conda_command=text("conda_command"),
        environment_variables=variables,
        readiness_command=argv_list("readiness_command"),
        version_probe_command=argv_list("version_probe_command"),
    )


def load_local_config(
    path: Path | None, *, environment: Mapping[str, str] | None = None
) -> dict[str, EnvironmentOverrides]:
    """Load machine-specific overrides keyed by system name.

    When ``path`` is ``None`` the default ``evaluation/local.yaml`` is used if
    it exists; a missing default is not an error (all overrides stay empty),
    while an explicitly requested missing file fails closed.

    Args:
        path: Explicit local config path, or ``None`` for the default.
        environment: Harness environment consulted for
            ``ROBOGUIDE_EVAL_CONFIG``; defaults to ``os.environ``.

    Returns:
        Overrides per system name.

    Raises:
        EvaluationConfigError: If an explicit config path is missing, the
            file is malformed, or its schema/version is unsupported.
    """
    source = environment if environment is not None else os.environ
    resolved = path
    if resolved is None:
        configured = source.get(LOCAL_CONFIG_ENVIRONMENT_VARIABLE)
        resolved = Path(configured) if configured else DEFAULT_LOCAL_CONFIG_PATH
    if not resolved.is_file():
        if path is not None or source.get(LOCAL_CONFIG_ENVIRONMENT_VARIABLE):
            raise EvaluationConfigError(f"local eval config not found: {resolved}")
        return {}
    try:
        document = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except OSError as error:
        raise EvaluationConfigError(f"cannot read local eval config {resolved}: {error}") from error
    except yaml.YAMLError as error:
        raise EvaluationConfigError(
            f"local eval config {resolved} is not valid YAML: {error}"
        ) from error
    table = _table(document, str(resolved))
    _required_keys(table, {"schema"}, {"environments"}, str(resolved))
    schema = table.get("schema")
    if schema != LOCAL_CONFIG_SCHEMA:
        raise EvaluationConfigError(
            f"unsupported local config schema {schema!r}; expected {LOCAL_CONFIG_SCHEMA!r}"
        )
    environments = _table(table.get("environments", {}), f"{resolved}.environments")
    return {
        name: _parse_overrides(
            _table(value, f"{resolved}.environments.{name}"), f"{resolved}.environments.{name}"
        )
        for name, value in environments.items()
    }


def _apply_environment_overrides(
    overrides: EnvironmentOverrides, system: str, environment: Mapping[str, str]
) -> EnvironmentOverrides:
    """Apply ``ROBOGUIDE_EVAL_<SYSTEM>_*`` variable overrides on top of a file.

    Args:
        overrides: Overrides loaded from the local config file.
        system: System under test name used to build variable names.
        environment: Harness environment supplying the overrides.

    Returns:
        Overrides with any environment-variable values taking precedence.
    """
    prefix = f"ROBOGUIDE_EVAL_{system.upper().replace('-', '_')}_"
    workdir = environment.get(f"{prefix}WORKDIR")
    executable = environment.get(f"{prefix}EXECUTABLE")
    conda = environment.get(f"{prefix}CONDA_ENV")
    return EnvironmentOverrides(
        working_directory=workdir or overrides.working_directory,
        executable=executable or overrides.executable,
        arguments=overrides.arguments,
        conda_environment=conda or overrides.conda_environment,
        conda_command=overrides.conda_command,
        environment_variables=overrides.environment_variables,
        readiness_command=overrides.readiness_command,
        version_probe_command=overrides.version_probe_command,
    )


def resolve_process_spec(
    spec: ExperimentSpec,
    system: str,
    overrides: EnvironmentOverrides,
    *,
    environment: Mapping[str, str] | None = None,
) -> ProcessSpec:
    """Merge all configuration layers into one executable process specification.

    Precedence is experiment spec defaults < local config < environment
    variables. When a Conda environment is configured, the final argv is
    wrapped with the ``conda run`` prefix so no interactive activation is
    ever required.

    Args:
        spec: The validated experiment specification.
        system: System under test to resolve.
        overrides: Machine-specific overrides for the system.
        environment: Harness environment used for variable overrides;
            defaults to ``os.environ``.

    Returns:
        The fully resolved :class:`ProcessSpec`.

    Raises:
        ExperimentSpecError: If the system is not declared by the spec.
        EvaluationConfigError: If required machine-specific fields (working
            directory, executable) are still missing after merging.
    """
    source = environment if environment is not None else os.environ
    environment_spec = spec.environment(system)
    merged = _apply_environment_overrides(overrides, system, source)
    workdir_hint = (
        f"working_directory (set {system}.working_directory in local config "
        f"or ROBOGUIDE_EVAL_{system.upper().replace('-', '_')}_WORKDIR)"
    )
    executable_hint = (
        f"executable (set {system}.executable in local config "
        f"or ROBOGUIDE_EVAL_{system.upper().replace('-', '_')}_EXECUTABLE)"
    )
    if not merged.working_directory or not merged.executable:
        missing = []
        if not merged.working_directory:
            missing.append(workdir_hint)
        if not merged.executable:
            missing.append(executable_hint)
        raise EvaluationConfigError(
            f"cannot resolve system {system!r}: missing " + "; ".join(missing)
        )
    working_directory = Path(merged.working_directory)
    if not working_directory.is_dir():
        raise EvaluationConfigError(
            f"system {system!r} working directory does not exist: {working_directory}"
        )
    arguments = merged.arguments if merged.arguments is not None else ()
    argv: list[str] = [merged.executable, *arguments]
    conda_command = merged.conda_command or "conda"
    if merged.conda_environment:
        argv = list(conda_run_prefix(conda_command, merged.conda_environment)) + argv
    timeout = environment_spec.timeout_seconds or spec.timeout_seconds
    return ProcessSpec(
        argv=tuple(argv),
        working_directory=working_directory,
        environment_overrides=merged.environment_variables or {},
        timeout_seconds=timeout,
        conda_environment=merged.conda_environment,
        readiness_command=merged.readiness_command,
        version_probe_command=merged.version_probe_command,
    )
