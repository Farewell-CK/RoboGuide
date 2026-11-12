"""Only complete option names can select connection, credential and initialization inputs."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from roboguide_workbench.cli import main, parser
from roboguide_workbench.configuration import apply_profile, global_config_path


@pytest.mark.parametrize(
    "argv",
    [
        ["chat", "--cont", "http://127.0.0.1:9002"],
        ["chat", "--ten", "explicit-tenant"],
        ["chat", "--conf", "explicit-model.toml"],
        ["chat", "--mission-ser", "http://127.0.0.1:9003"],
        ["chat", "--he"],
        ["init", "--api-key-e", "EXPLICIT_KEY"],
        ["init", "--no-au"],
        ["nodes", "--cont", "http://127.0.0.1:9002"],
        ["doctor", "--conf", "explicit-model.toml"],
    ],
)
def test_abbreviated_options_refuse_before_setup_or_service_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Rejected spellings cannot silently choose a profile value or persist first-run defaults."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    def unexpected(*args: object, **kwargs: object) -> None:
        """Any initialization or service construction after invalid arguments is a regression."""
        pytest.fail("invalid abbreviated options reached initialization or service access")

    monkeypatch.setattr("roboguide_workbench.cli.ReadOnlyController", unexpected)
    monkeypatch.setattr("roboguide_workbench.onboarding.interactive_setup", unexpected)
    with patch.object(sys.stdin, "isatty", return_value=True):
        with pytest.raises(SystemExit) as refused:
            main(argv)
    assert refused.value.code == 2
    assert not (tmp_path / "config-home").exists()
    assert not (tmp_path / "state-home").exists()


def test_complete_explicit_options_keep_precedence_over_global_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The complete supported spellings retain the user's exact origin, identity and model path."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    profile = global_config_path()
    profile.parent.mkdir(parents=True)
    profile.write_text(
        '[workbench]\ncontroller="http://127.0.0.1:9001"\ntenant="configured-tenant"\n'
        'config="configured-model.toml"\n'
    )
    selected_model = tmp_path / "explicit-model.toml"
    argv = [
        "chat",
        "--controller",
        "http://127.0.0.1:9002",
        "--tenant=explicit-tenant",
        "--config",
        str(selected_model),
    ]
    arguments = parser().parse_args(argv)
    apply_profile(arguments, argv)
    assert arguments.controller == "http://127.0.0.1:9002"
    assert arguments.tenant == "explicit-tenant"
    assert arguments.config == selected_model
