"""Shared deterministic helpers for Eval Harness tests.

All child "systems under test" are synthetic Python processes; no Conda,
network, or repository Git state is consulted. Import this module directly
from tests; pytest puts the tests directory on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

FIXTURE_METRICS_SCRIPT: str = """\
import json
import os
import sys

output_dir = sys.argv[1]
os.makedirs(output_dir, exist_ok=True)
with open(os.path.join(output_dir, "raw-metrics.json"), "w", encoding="utf-8") as handle:
    json.dump(
        {
            "values": {"success": True, "wall_time": 1.5},
            "details": {"note": "fixture", "steps": 42},
        },
        handle,
    )
with open(os.path.join(output_dir, "episode.log"), "w", encoding="utf-8") as handle:
    handle.write("fixture episode ran\\n")
print("fixture-stdout")
sys.stdout.flush()
print("fixture-stderr", file=sys.stderr)
"""

FIXTURE_FAILING_SCRIPT: str = """\
import json
import os
import sys

output_dir = sys.argv[1]
os.makedirs(output_dir, exist_ok=True)
with open(os.path.join(output_dir, "raw-metrics.json"), "w", encoding="utf-8") as handle:
    json.dump({"values": {"success": False, "wall_time": 0.25}}, handle)
print("about to fail", file=sys.stderr)
sys.exit(2)
"""

FIXTURE_SLEEPING_SCRIPT: str = """\
import sys
import time

time.sleep(float(sys.argv[1]))
print("finally done")
"""

FIXTURE_ENV_SCRIPT: str = """\
import os
import sys

print(os.environ["FIXTURE_TOKEN"])
"""

FIXTURE_EMOS_LOG_SCRIPT: str = """\
import sys

print("INFO habitat_baselines.rl.multi_agent.habitat_mas_evaluator - "
      "Average episode pddl_success: 1.0000")
print("Average episode composite_success: 0.5000")
print("Average episode reward: 12.2500")
sys.exit(0)
"""

# Mirrors real EMOS shape: many kilobytes of discussion logging before the
# evaluator's summary lines, so truncating readers lose the metrics.
FIXTURE_EMOS_LONG_LOG_SCRIPT: str = """\
import sys

for turn in range(120):
    print(f"INFO habitat.mas discussion turn {turn:03d}: " + "token " * 15)
print("INFO evaluator - Average episode pddl_success: 1.0000")
print("Average episode composite_success: 0.5000")
sys.exit(0)
"""


def interpreter() -> str:
    """Return the current Python interpreter path for child processes.

    Returns:
        The absolute path of the running interpreter.
    """
    return sys.executable


def local_config_yaml(
    working_directory: Path,
    *,
    executable: str | None = None,
    arguments: list[str] | None = None,
    extra_lines: str = "",
    environment_variables: dict[str, str] | None = None,
) -> str:
    """Build local configuration YAML text for the fixture ``emos`` system.

    The document is rendered with ``yaml.safe_dump`` so multi-line argument
    strings (fixture child scripts) stay valid YAML.

    Args:
        working_directory: Existing directory used as the system workdir.
        executable: Executable override; defaults to the test interpreter.
        arguments: Argument override; defaults to the fixture metrics script.
        extra_lines: Additional raw YAML lines appended inside the emos
            environment block (for example Conda settings).
        environment_variables: Explicit child environment overrides rendered
            as a YAML mapping (values may contain ``${VAR}`` references).

    Returns:
        Local config YAML text.
    """
    resolved_executable = executable if executable is not None else interpreter()
    resolved_arguments = (
        arguments if arguments is not None else ["-u", "-c", FIXTURE_METRICS_SCRIPT, "{output_dir}"]
    )
    emos: dict[str, object] = {
        "working_directory": working_directory.as_posix(),
        "executable": resolved_executable,
        "arguments": resolved_arguments,
        "version_probe_command": [interpreter(), "-c", "print('v-fixture-1.2.3')"],
    }
    if environment_variables:
        emos["environment_variables"] = environment_variables
    document = {"schema": "roboguide-eval.local-config/v0.1", "environments": {"emos": emos}}
    rendered = yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=1000)
    return rendered + extra_lines


BASE_SPEC_YAML: str = """\
schema: roboguide-eval.experiment-spec/v0.1
experiment_id: fixture-experiment
description: offline fixture experiment
benchmark: fixture-bench
task: mobility
dataset:
  name: fixture-dataset
  revision: r1
context:
  robots: spot+fetch
episodes: [ep-000, ep-001]
systems: [emos]
llm:
  provider: openai
  model: gpt-5.6-luna
  reasoning_effort: medium
  reasoning_options:
    summary: auto
seeds: [7, 11]
metrics: [success, wall_time]
timeout_seconds: 60
environments:
  emos:
    timeout_seconds: 30
    metrics_source_path: raw-metrics.json
    expected_output_paths: [raw-metrics.json, episode.log]
"""

BASE_TIMEOUT_SPEC_YAML: str = """\
schema: roboguide-eval.experiment-spec/v0.1
experiment_id: fixture-timeout-experiment
benchmark: fixture-bench
task: mobility
episodes: [ep-slow]
systems: [emos]
llm:
  provider: openai
  model: gpt-5.6-luna
seeds: [1]
metrics: [success]
timeout_seconds: 1
environments:
  emos:
    metrics_source_path: raw-metrics.json
    expected_output_paths: [raw-metrics.json]
"""
