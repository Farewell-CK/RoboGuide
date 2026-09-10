"""Shared deterministic helpers for Eval Harness tests.

All child "systems under test" are synthetic Python processes; no Conda,
network, or repository Git state is consulted. Import this module directly
from tests; pytest puts the tests directory on ``sys.path``.
"""

from __future__ import annotations

import gzip
import json
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

print("========================Episode Step Info==========================")
print("Episode ID: 5, Num Steps: 120")
print("===================================================================")
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
print("Episode ID: 9, Num Steps: 480")
print("INFO evaluator - Average episode pddl_success: 1.0000")
print("Average episode composite_success: 0.5000")
sys.exit(0)
"""

# Official banner plus EMOS's own token records at the REAL nested path
# chat_history_output/<date>/<config>/<ablation>/<episode_id>/ (as
# constructed by MultiLLMPolicy.act): token_usage.json (per-agent totals)
# and token_usage_details.jsonl (per-call usage from the accounting
# instrumentation). String concatenation (not f-strings) avoids colliding
# with harness {placeholders} that would substitute declared names like
# episode_id inside argv.
FIXTURE_EMOS_EPISODE_SCRIPT: str = """\
import json
import os
import sys

episode_id = "42"
print("========================Episode Step Info==========================")
print("Episode ID: " + episode_id + ", Num Steps: 512")
print("===================================================================")
token_dir = os.path.join(
    "chat_history_output", "2026-09-09", "llm_fixture", "FULL", episode_id
)
os.makedirs(token_dir, exist_ok=True)
with open(os.path.join(token_dir, "token_usage.json"), "w", encoding="utf-8") as handle:
    json.dump({"agent_0": 2200, "agent_1": 800}, handle)
details = [
    {
        "agent_name": "agent_0",
        "model": "gpt-5.6-luna",
        "request_utc": "2026-09-09T10:30:00",
        "response_utc": "2026-09-09T10:30:41",
        "latency_ms": 41000.0,
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 300,
            "total_tokens": 1200,
            "prompt_tokens_details": {"cached_tokens": 400},
            "completion_tokens_details": {"reasoning_tokens": 50},
        },
    },
    {
        "agent_name": "agent_0",
        "model": "gpt-5.6-luna",
        "request_utc": "2026-09-09T10:31:00",
        "response_utc": "2026-09-09T10:31:12",
        "latency_ms": 12000.0,
        "usage": {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000},
    },
    {
        "agent_name": "agent_1",
        "model": "gpt-5.6-luna",
        "request_utc": "2026-09-09T10:32:00",
        "response_utc": "2026-09-09T10:32:30",
        "latency_ms": 30000.0,
        "usage": {"prompt_tokens": 600, "completion_tokens": 200, "total_tokens": 800},
    },
]
with open(os.path.join(token_dir, "token_usage_details.jsonl"), "w", encoding="utf-8") as handle:
    for record in details:
        handle.write(json.dumps(record) + "\\n")
print("INFO evaluator - Average episode pddl_success: 1.0000")
print("Average episode composite_success: 0.5000")
sys.exit(0)
"""

# A multi-episode batch run: two official banners, averaged success.
FIXTURE_EMOS_BATCH_SCRIPT: str = """\
import sys

print("Episode ID: 3, Num Steps: 300")
print("Episode ID: 4, Num Steps: 400")
print("Average episode pddl_success: 0.6000")
sys.exit(0)
"""

# Mirrors the REAL successful smoke shape: evaluator summary lines arrive on
# stderr through Python logging with timestamp prefixes, and the official
# stage-goal aggregates (pddl_stage_goals.<stage>_success) are among them.
FIXTURE_EMOS_STDERR_LOG_SCRIPT: str = """\
import sys

print("Episode ID: 6, Num Steps: 150")
print("2026-09-09 10:25:35,220 Average episode rearrange_cooperate_reward: 0.0000",
      file=sys.stderr)
print("2026-09-09 10:25:35,221 Average episode pddl_stage_goals.robot_at_object_0_success: 1.0000",
      file=sys.stderr)
receptacle_line = (
    "2026-09-09 10:25:35,221 Average episode "
    "pddl_stage_goals.robot_at_receptacle_0_success: 0.0000"
)
print(receptacle_line, file=sys.stderr)
print("2026-09-09 10:25:35,221 Average episode num_steps: 150.0000", file=sys.stderr)
print("2026-09-09 10:25:35,221 Average episode pddl_success: 1.0000", file=sys.stderr)
sys.exit(0)
"""

# No stdout banner; instead appends one new record per invocation to the
# EMOS-style persistent episode_log step file (unique id via a counter), so
# the fallback identity path must attribute each run only its own record.
# String concatenation avoids colliding with harness {placeholders}.
FIXTURE_EMOS_APPEND_LOG_SCRIPT: str = """\
import json
import os

counter_path = os.path.join(os.getcwd(), "append_counter.txt")
episode_number = 11
if os.path.exists(counter_path):
    with open(counter_path, encoding="utf-8") as handle:
        episode_number = int(handle.read().strip()) + 1
with open(counter_path, "w", encoding="utf-8") as handle:
    handle.write(str(episode_number))
log_path = os.path.join("episode_log", "fixture", "FULL", "fixture_steps_log.json")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
data = {}
if os.path.exists(log_path):
    with open(log_path, encoding="utf-8") as handle:
        data = json.load(handle)
data["episode_id: " + str(episode_number)] = "num_steps: 77"
with open(log_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle)
"""

# Writes a canonical-contract-violating raw metrics file (rate out of [0,1])
# to probe the degrade-with-evidence path.
FIXTURE_INVALID_METRICS_SCRIPT: str = """\
import json
import os
import sys

output_dir = sys.argv[1]
with open(os.path.join(output_dir, "raw-metrics.json"), "w", encoding="utf-8") as handle:
    json.dump({"values": {"subgoal_success_rate": 3.0}}, handle)
print("wrote invalid raw metrics")
"""


def interpreter() -> str:
    """Return the current Python interpreter path for child processes.

    Returns:
        The absolute path of the running interpreter.
    """
    return sys.executable


def write_fixture_dataset(
    path: Path,
    episodes: list[dict[str, object]],
) -> str:
    """Write a gzipped fixture dataset and return its SHA-256 digest.

    Args:
        path: Target dataset file path (``.json.gz``).
        episodes: Episode records with ``episode_id`` and ``scene_id``.

    Returns:
        The lowercase hexadecimal digest of the written file bytes, matching
        how the resolver digests pinned datasets.
    """
    import hashlib

    payload = json.dumps({"episodes": episodes}).encode(encoding="utf-8")
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
