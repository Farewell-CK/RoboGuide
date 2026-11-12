"""Project files cannot silently replace a configured Provider's credential destination."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from roboguide_workbench.cli import parser
from roboguide_workbench.configuration import apply_profile, global_config_path
from roboguide_workbench.onboarding import write_configuration
from test_workbench import descriptor, http_fixture


def reply() -> bytes:
    """Return a complete finite clarification requiring no Controller or follow-up model call."""
    return json.dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "queries": [descriptor("clarify", message="请明确任务")],
                                    "answer": "",
                                    "citations": [],
                                }
                            ),
                        }
                    ],
                }
            ],
        }
    ).encode()


def model_file(path: Path, endpoint: str) -> None:
    """Name an inherited credential in a project-owned Provider profile."""
    path.write_text(
        "[model]\nbase_url=" + json.dumps(endpoint) + '\nname="fixture"\n'
        'api_key_env="RG_REVIEW_KEY"\nrequires_auth=true\n'
    )


@pytest.mark.parametrize("global_ready", [False, True])
def test_bare_installed_chat_never_sends_credentials_to_implicit_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_ready: bool
) -> None:
    """Both project profile and legacy model fallback stay inert before explicit user selection."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "global-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "global-state"))
    monkeypatch.setenv("RG_REVIEW_KEY", "review-test-sentinel")
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    monkeypatch.chdir(project)
    with (
        http_fixture(reply()) as (trusted, trusted_calls),
        http_fixture(reply()) as (untrusted, untrusted_calls),
    ):
        if global_ready:
            write_configuration(base_url=trusted, model="fixture", api_key_env="RG_REVIEW_KEY")
        model_file(project / "config/project-model.toml", untrusted)
        model_file(project / "config/mission.toml", untrusted)
        (project / "config/workbench.toml").write_text(
            '[workbench]\nconfig="project-model.toml"\ncontroller="http://127.0.0.1:9001"\n'
        )
        command = [str(Path(sys.executable).parent / "roboguide"), "chat", "--ask", "请帮我规划"]
        result = subprocess.run(
            command,
            cwd=project,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == (0 if global_ready else 2), result.stderr
        assert untrusted_calls == []
        assert len(trusted_calls) == (1 if global_ready else 0)
        args = parser().parse_args(["chat"])
        apply_profile(args, ["chat"])
        assert args.config == global_config_path().with_name("model.toml")
        assert args.controller == "http://127.0.0.1:8080"
        # Explicit selection is an intentional source choice and still supports project profiles.
        selected = subprocess.run(
            command + ["--profile", "config/workbench.toml"],
            cwd=project,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert selected.returncode == 0, selected.stderr
        assert len(untrusted_calls) == 1
