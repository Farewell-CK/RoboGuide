"""Installed conversation startup uses persistent private settings without repository assets."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from mission.config import MissionSettings
from mission.models import JSONObject
from roboguide_workbench import onboarding
from roboguide_workbench.cli import main, parser
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.configuration import (
    apply_profile,
    global_config_path,
    global_state_directory,
)
from roboguide_workbench.onboarding import interactive_setup, write_configuration
from roboguide_workbench.provider_config import ChatSettings, load_chat_settings, model_settings
from test_workbench import ROOT, descriptor, http_fixture, inventory, node


@pytest.fixture
def isolated_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use private task-owned XDG locations and an unrelated working directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    monkeypatch.delenv("ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP", raising=False)
    project = tmp_path / "unrelated"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


def test_installed_defaults_are_independent_of_working_directory(
    isolated_defaults: Path, tmp_path: Path
) -> None:
    """An installed client selects persistent model and session paths before any settings exist."""
    args = parser().parse_args(["chat"])
    apply_profile(args, ["chat"])
    assert args.profile is None
    assert args.config == tmp_path / "config-home/roboguide/model.toml"
    assert args.session_dir == tmp_path / "state-home/roboguide/sessions"
    assert not (isolated_defaults / "var").exists()


def test_implicit_project_configuration_cannot_select_credentials(isolated_defaults: Path) -> None:
    """A project-owned model file requires explicit selection before it can read any credential."""
    path = isolated_defaults / "config/mission.toml"
    path.parent.mkdir()
    path.write_text("# Existing project-owned Mission configuration\n")
    args = parser().parse_args(["chat"])
    apply_profile(args, ["chat"])
    assert args.config == global_config_path().with_name("model.toml")
    assert args.session_dir == global_state_directory() / "sessions"


def test_project_profile_and_explicit_flags_override_global_configuration(
    isolated_defaults: Path,
) -> None:
    """Local selection and CLI overrides retain deterministic precedence and relative paths."""
    write_configuration(base_url="https://provider.example/v1", model="fixture-model")
    project = isolated_defaults / "config/workbench.toml"
    project.parent.mkdir()
    project.write_text(
        '[workbench]\ncontroller="http://127.0.0.1:9000"\nconfig="local-model.toml"\n'
    )
    argv = ["chat", "--profile", "config/workbench.toml", "--controller=http://127.0.0.1:9001"]
    args = parser().parse_args(argv)
    apply_profile(args, argv)
    assert args.profile == Path("config/workbench.toml")
    assert args.controller == "http://127.0.0.1:9001"
    assert args.config == project.with_name("local-model.toml")


def test_generated_configuration_is_private_and_never_copies_the_key(
    isolated_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup stores the credential variable's name while never reading or embedding its value."""
    secret = "bootstrap-private-sentinel-value"
    monkeypatch.setenv("RG_BOOTSTRAP_TEST_KEY", secret)
    path = write_configuration(
        base_url="https://provider.example/v1",
        model="fixture-model",
        api_key_env="RG_BOOTSTRAP_TEST_KEY",
    )
    assert path == global_config_path()
    model_path = path.with_name("model.toml")
    assert path.stat().st_mode & 0o777 == 0o600
    assert model_path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert secret not in path.read_text() + model_path.read_text()
    settings = load_chat_settings(model_path)
    assert isinstance(settings, ChatSettings)
    assert settings.provider.api_key_env == "RG_BOOTSTRAP_TEST_KEY"
    assert settings.provider.api_key(os.environ) == secret
    assert settings.provider.endpoint({}) == "https://provider.example/v1/responses"
    assert settings.llm.model == "fixture-model"
    args = parser().parse_args(["chat"])
    apply_profile(args, ["chat"])
    assert args.config == model_path and args.session_dir == global_state_directory() / "sessions"


def test_first_run_wizard_saves_only_explicit_connection_settings(
    isolated_defaults: Path,
) -> None:
    """The local wizard can complete without network access, backend startup or a credential."""
    replies = iter(["", "", "http://127.0.0.1:8123/v1", "local-model", "LOCAL_MODEL_KEY", "n"])
    questions: list[str] = []
    messages: list[str] = []

    def enter(question: str) -> str:
        """Supply bounded original answers and record that no secret value was requested."""
        questions.append(question)
        return next(replies)

    path = interactive_setup(input_text=enter, emit=messages.append)
    assert len(questions) == 6 and any("环境变量名" in question for question in questions)
    settings = load_chat_settings(path.with_name("model.toml"))
    assert settings.provider.api_key({}) is None and settings.llm.model == "local-model"
    assert any("任意目录" in message for message in messages)
    assert not (isolated_defaults / "var").exists()


def test_initializer_does_not_replace_existing_settings(isolated_defaults: Path) -> None:
    """An existing model/profile pair stays byte-for-byte unchanged after another init attempt."""
    path = write_configuration(base_url="https://provider.example/v1", model="first")
    original = path.read_bytes(), path.with_name("model.toml").read_bytes()
    with pytest.raises(WorkbenchError, match="未覆盖"):
        write_configuration(base_url="https://provider.example/v1", model="second")
    assert original == (path.read_bytes(), path.with_name("model.toml").read_bytes())


def test_installed_init_can_configure_once_without_a_repository(
    isolated_defaults: Path,
) -> None:
    """Installed noninteractive init publishes reusable settings without calling any service."""
    entry = str(Path(sys.executable).parent / "roboguide")
    initialized = subprocess.run(
        [entry, "init", "--base-url", "http://127.0.0.1:9/v1", "--model", "fixture", "--no-auth"],
        cwd=isolated_defaults,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert initialized.returncode == 0, initialized.stderr
    for name in ("first-directory", "second-directory"):
        unrelated = isolated_defaults / name
        unrelated.mkdir()
        help_result = subprocess.run(
            [entry, "chat", "--ask", "/help"],
            cwd=unrelated,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert help_result.returncode == 0, help_result.stderr
        assert "/nodes" in help_result.stdout and "首次配置" not in help_result.stdout
    assert len(list((global_state_directory() / "sessions").glob("*.json"))) == 2


def test_first_run_chat_wizard_is_not_repeated_after_global_configuration(
    isolated_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTY startup configures once, then discovers that same profile from an unrelated directory."""
    replies = iter(["", "", "http://127.0.0.1:9/v1", "fixture", "OPENAI_API_KEY", "n", "/quit"])
    questions: list[str] = []

    def enter(question: str) -> str:
        """Drive first-run input and ordinary dialogue without opening an interactive TTY."""
        questions.append(question)
        return next(replies)

    monkeypatch.setattr("builtins.input", enter)
    with patch.object(sys.stdin, "isatty", return_value=True):
        assert main(["chat", "--plain"]) == 0
    assert global_config_path().exists() and len(questions) == 7
    other = isolated_defaults / "another-project"
    other.mkdir()
    monkeypatch.chdir(other)
    questions.clear()
    replies = iter(["/quit"])
    with patch.object(sys.stdin, "isatty", return_value=True):
        assert main(["chat", "--plain"]) == 0
    assert len(questions) == 1


def test_profile_publish_failure_removes_only_our_unreferenced_model(
    isolated_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed profile publication leaves initialization retryable without stale model bytes."""
    original = onboarding._publish

    def fail_profile(path: Path, content: str) -> tuple[int, int]:
        """Fail the final publication after the independent model was actually written."""
        if path.name == "config.toml":
            raise OSError("injected profile publication failure")
        return original(path, content)

    monkeypatch.setattr(onboarding, "_publish", fail_profile)
    with pytest.raises(OSError, match="publication failure"):
        write_configuration(base_url="https://provider.example/v1", model="fixture")
    assert not global_config_path().exists()
    assert not global_config_path().with_name("model.toml").exists()


def test_remote_controller_still_requires_actual_mtls_before_setup_writes(
    isolated_defaults: Path,
) -> None:
    """Onboarding cannot bypass the fixed remote Controller TLS policy or fabricate credentials."""
    with pytest.raises(WorkbenchError, match="mTLS"):
        write_configuration(
            base_url="https://provider.example/v1",
            model="fixture",
            controller="https://controller.example:8080",
        )
    assert not global_config_path().exists()


@pytest.mark.parametrize(
    "changed",
    [
        {"base_url": "https://user:password@provider.example/v1"},
        {"base_url": "http://provider.example/v1"},
        {"responses_path": "https://other.example/responses"},
        {"api_key_env": "KEY; echo invalid"},
        {"requires_auth": 1},
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 601},
        {"unknown_key": "not accepted"},
    ],
)
def test_standalone_model_profile_keeps_closed_safe_inputs(
    isolated_defaults: Path, changed: dict[str, object]
) -> None:
    """An installation-friendly schema does not widen credential, origin or deadline policy."""
    values: dict[str, object] = {"base_url": "https://provider.example/v1", "name": "fixture"}
    values.update(changed)
    with pytest.raises(ValueError):
        model_settings(values)


def test_legacy_mission_configuration_remains_supported(isolated_defaults: Path) -> None:
    """Existing full Mission TOML uses its original parser rather than a lossy model projection."""
    settings = load_chat_settings(ROOT / "config/mission.toml")
    assert isinstance(settings, MissionSettings)
    assert settings.prompts.interpreter_path == ROOT / "mission/prompts/v0/interpreter.md"


def test_installed_cli_uses_global_model_from_unrelated_directory(
    isolated_defaults: Path,
) -> None:
    """The installed entry crosses local Provider and Controller HTTP without project assets."""
    reply: JSONObject = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(descriptor("cluster"))}],
            }
        ],
    }
    with (
        http_fixture(json.dumps(reply).encode()) as (provider, provider_calls),
        http_fixture(json.dumps(inventory(node("actual-fixture-node"))).encode()) as (
            controller,
            controller_calls,
        ),
    ):
        write_configuration(
            base_url=provider,
            model="fixture-only",
            controller=controller,
            requires_auth=False,
        )
        run = subprocess.run(
            [
                str(Path(sys.executable).parent / "roboguide"),
                "chat",
                "--answer-mode",
                "facts",
                "--ask",
                "集群有哪些节点？",
            ],
            cwd=isolated_defaults,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    assert run.returncode == 0, run.stderr
    assert "actual-fixture-node" in run.stdout
    assert len(provider_calls) == len(controller_calls) == 1
    assert provider_calls[0][0] == "POST" and controller_calls[0][0] == "GET"
    assert list((global_state_directory() / "sessions").glob("*.json"))
    assert not (isolated_defaults / "var").exists()
