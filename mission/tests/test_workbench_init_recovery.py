"""Initialization preserves explicit inputs and recovers exact owned publication failures."""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from roboguide_workbench import onboarding
from roboguide_workbench.cli import main
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.configuration import global_config_path
from roboguide_workbench.onboarding import interactive_setup, write_configuration


@pytest.fixture
def private_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every configuration, state and working path in this test's private directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    return tmp_path


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4])
def test_each_file_and_directory_fsync_failure_remains_retryable(
    private_defaults: Path, monkeypatch: pytest.MonkeyPatch, failure_at: int
) -> None:
    """A one-shot failure before or after either hard link leaves no incomplete public names."""
    actual = os.fsync
    calls = 0

    def fail_once(descriptor: int) -> None:
        """Fail one actual file or directory durability boundary after prior effects occurred."""
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected initialization durability failure")
        actual(descriptor)

    with monkeypatch.context() as faults:
        faults.setattr(os, "fsync", fail_once)
        with pytest.raises(OSError, match="durability failure"):
            write_configuration(base_url="https://provider.example/v1", model="model")
    profile = global_config_path()
    assert not profile.exists() and not profile.with_name("model.toml").exists()
    assert not list(profile.parent.glob(".init-*"))
    retried = write_configuration(base_url="https://provider.example/v1", model="model")
    assert retried == profile
    assert tomllib.loads(profile.read_text())["workbench"]["config"] == "model.toml"
    assert tomllib.loads(profile.with_name("model.toml").read_text())["model"]["name"] == "model"
    assert profile.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure_at", [1, 2])
def test_link_failure_cleans_only_own_unreferenced_files(
    private_defaults: Path, monkeypatch: pytest.MonkeyPatch, failure_at: int
) -> None:
    """An unavailable publication link never strands its previously published model file."""
    original = os.link
    calls = 0

    def unavailable(source: str, target: Path) -> None:
        """Stop before the selected atomic link while allowing all original earlier effects."""
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected link refusal")
        original(source, target)

    with monkeypatch.context() as faults:
        faults.setattr(os, "link", unavailable)
        with pytest.raises(OSError, match="link refusal"):
            write_configuration(base_url="https://provider.example/v1", model="model")
    profile = global_config_path()
    assert not profile.exists() and not profile.with_name("model.toml").exists()
    assert not list(profile.parent.glob(".init-*"))
    assert write_configuration(base_url="https://provider.example/v1", model="model") == profile


@pytest.mark.parametrize("target_name", ["model.toml", "config.toml"])
def test_durability_cleanup_never_removes_a_replaced_inode(
    private_defaults: Path, monkeypatch: pytest.MonkeyPatch, target_name: str
) -> None:
    """Another initializer's replacement is preserved after our own linked inode is displaced."""
    actual = os.fsync
    directory_syncs = 0
    profile = global_config_path()
    replacement_bytes = b"# independently replaced original bytes\n"

    def replace_after_link(descriptor: int) -> None:
        """Replace one linked name before making that publication's directory sync fail."""
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_syncs += 1
            wanted = 1 if target_name == "model.toml" else 2
            if directory_syncs == wanted:
                replacement = profile.with_name(target_name + ".replacement")
                replacement.write_bytes(replacement_bytes)
                replacement.chmod(0o600)
                replacement.replace(profile.with_name(target_name))
                raise OSError("injected displaced publication")
        actual(descriptor)

    with monkeypatch.context() as faults:
        faults.setattr(os, "fsync", replace_after_link)
        with pytest.raises(OSError, match="displaced publication"):
            write_configuration(base_url="https://provider.example/v1", model="model")
    assert profile.with_name(target_name).read_bytes() == replacement_bytes
    if target_name == "config.toml":
        assert profile.with_name("model.toml").exists()
    assert not list(profile.parent.glob(".init-*"))


def test_unremovable_published_profile_keeps_its_complete_model(
    private_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If rollback itself is unavailable, retain a consistent pair instead of deleting its model."""
    original_sync, original_unlink = os.fsync, Path.unlink
    calls = 0
    profile = global_config_path()

    def fail_profile_sync(descriptor: int) -> None:
        """Fail only the public profile's directory sync after its complete file was linked."""
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("profile durability failed")
        original_sync(descriptor)

    def refuse_profile_cleanup(path: Path, missing_ok: bool = False) -> None:
        """Retain the actual published complete profile when unlink is unavailable."""
        if path == profile:
            raise OSError("profile cleanup unavailable")
        original_unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as faults:
        faults.setattr(os, "fsync", fail_profile_sync)
        faults.setattr(Path, "unlink", refuse_profile_cleanup)
        with pytest.raises(OSError, match="cleanup unavailable"):
            write_configuration(base_url="https://provider.example/v1", model="model")
    assert tomllib.loads(profile.read_text())["workbench"]["config"] == "model.toml"
    assert tomllib.loads(profile.with_name("model.toml").read_text())["model"]["name"] == "model"
    with pytest.raises(WorkbenchError, match="未覆盖"):
        write_configuration(base_url="https://provider.example/v1", model="replacement")


@pytest.mark.parametrize("command", ["init", "chat"])
def test_interactive_setup_keeps_explicit_connection_and_identity_inputs(
    private_defaults: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """Bare future chat keeps the successful first-run invocation's endpoints and tenant."""
    questions: list[str] = []
    replies = iter(
        ["http://127.0.0.1:9/v1", "fixture"]
        + (["OPENAI_API_KEY", "n", "/quit"] if command == "chat" else [])
    )

    def enter(question: str) -> str:
        """Provide absent model inputs without requesting explicit connection options again."""
        questions.append(question)
        return next(replies)

    monkeypatch.setattr("builtins.input", enter)
    argv = [
        command,
        "--controller",
        "http://127.0.0.1:9001",
        "--mission-service",
        "http://127.0.0.1:9002",
        "--tenant",
        "team-explicit",
    ]
    if command == "init":
        argv += ["--api-key-env", "SPECIAL_KEY", "--no-auth"]
    else:
        argv += ["--plain"]
    with patch.object(sys.stdin, "isatty", return_value=True):
        assert main(argv) == 0
    saved = tomllib.loads(global_config_path().read_text())["workbench"]
    model = tomllib.loads(global_config_path().with_name("model.toml").read_text())["model"]
    assert saved["controller"] == "http://127.0.0.1:9001"
    assert saved["mission_service"] == "http://127.0.0.1:9002"
    assert saved["tenant"] == "team-explicit"
    assert model["api_key_env"] == ("SPECIAL_KEY" if command == "init" else "OPENAI_API_KEY")
    assert model["requires_auth"] is False
    assert not any(
        "Controller 地址" in text or "Mission Service 地址" in text for text in questions
    )
    # A later bare invocation reads the preserved settings rather than the old unrelated defaults.
    replies = iter(["/quit"])
    with patch.object(sys.stdin, "isatty", return_value=True):
        assert main(["chat", "--plain"]) == 0


def test_explicit_tls_paths_are_not_prompted_or_rewritten(
    private_defaults: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wizard passes all original TLS inputs through the same existing validation boundary."""
    paths = {
        name: private_defaults / (name + ".pem")
        for name in (
            "ca",
            "client_cert",
            "client_key",
            "mission_ca",
            "mission_client_cert",
            "mission_client_key",
        )
    }
    initial: dict[str, object] = {
        "controller": "https://controller.example:8080",
        "mission_service": "https://mission.example:8070",
        "tenant": "tenant-a",
        "api_key_env": "EXPLICIT_KEY",
        "requires_auth": True,
        **paths,
    }
    captured: dict[str, object] = {}
    questions: list[str] = []
    answers = iter(["https://provider.example/v1", "model"])

    def enter(question: str) -> str:
        """Only the absent provider address and model remain interactive inputs."""
        questions.append(question)
        return next(answers)

    def capture(**values: object) -> Path:
        """Observe the existing writer's exact validation inputs without fabricating TLS files."""
        captured.update(values)
        return global_config_path()

    monkeypatch.setattr(onboarding, "write_configuration", capture)
    interactive_setup(initial=initial, input_text=enter, emit=lambda text: None)
    assert len(questions) == 2
    assert all(captured[key] == value for key, value in initial.items())


@pytest.mark.parametrize(
    "flag", ["--profile", "--config", "--session-dir", "--resume", "--ask", "--timeout"]
)
def test_init_rejects_options_it_cannot_persist(private_defaults: Path, flag: str) -> None:
    """Conversation-only arguments cannot silently succeed as ignored initialization inputs."""
    value = "5" if flag == "--timeout" else "unused"
    assert (
        main(["init", flag, value, "--base-url", "https://provider.example/v1", "--model", "model"])
        == 2
    )
    assert not global_config_path().exists()
