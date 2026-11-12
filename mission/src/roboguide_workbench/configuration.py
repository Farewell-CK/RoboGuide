"""Independent workbench connection profiles and origin-bound session scope."""

from __future__ import annotations

import argparse
import hashlib
import os
import tomllib
from pathlib import Path

from mission.models import JSONObject

from roboguide_workbench.client import WorkbenchError

FIELDS = {
    "controller",
    "tenant",
    "timeout",
    "ca",
    "client_cert",
    "client_key",
    "mission_service",
    "mission_timeout",
    "mission_ca",
    "mission_client_cert",
    "mission_client_key",
    "config",
    "session_dir",
    "answer_mode",
    "tool_limit",
    "call_limit",
    "turn_seconds",
}
PATHS = {
    "ca",
    "client_cert",
    "client_key",
    "mission_ca",
    "mission_client_cert",
    "mission_client_key",
    "config",
    "session_dir",
}


def global_config_path() -> Path:
    """Locate the user's persistent profile independently of the current working directory."""
    configured = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".config"
    )
    return base / "roboguide" / "config.toml"


def global_state_directory() -> Path:
    """Keep installed-client sessions in the user's state directory outside arbitrary projects."""
    configured = os.environ.get("XDG_STATE_HOME")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".local" / "state"
    )
    return base / "roboguide"


def explicit_option(argv: list[str], name: str) -> bool:
    """Recognize both standard command-line spellings without interpreting option values."""
    flag = "--" + name.replace("_", "-")
    return any(arg == flag or arg.startswith(flag + "=") for arg in argv)


def apply_profile(arguments: argparse.Namespace, argv: list[str]) -> None:
    """Load trusted global defaults or explicitly selected project settings before credentials."""
    selected = arguments.profile
    global_profile = global_config_path()
    if selected is None and global_profile.exists():
        selected = global_profile
    profile: dict[str, object] = {}
    if selected is not None:
        with selected.open("rb") as source:
            raw = tomllib.load(source)
        if set(raw) != {"workbench"} or not isinstance(raw["workbench"], dict):
            raise WorkbenchError("配置必须只包含 [workbench]。")
        profile = raw["workbench"]
        if set(profile) - FIELDS:
            raise WorkbenchError("工作台配置含未知字段。")
        arguments.profile = selected
    for key, value in profile.items():
        if explicit_option(argv, key):
            continue
        if key in {"timeout", "mission_timeout", "tool_limit", "call_limit", "turn_seconds"}:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise WorkbenchError("连接超时必须为数字。")
            if key in {"tool_limit", "call_limit"} and type(value) is not int:
                raise WorkbenchError("调用次数上限必须为整数。")
        elif not isinstance(value, str) or not value.strip():
            raise WorkbenchError("工作台配置文本字段不能为空。")
        if key in PATHS:
            value = (selected.parent / value).resolve()
        setattr(arguments, key, value)
    if (
        hasattr(arguments, "config")
        and not explicit_option(argv, "config")
        and "config" not in profile
    ):
        arguments.config = global_profile.with_name("model.toml")
    if (
        hasattr(arguments, "session_dir")
        and not explicit_option(argv, "session_dir")
        and "session_dir" not in profile
    ):
        arguments.session_dir = global_state_directory() / "sessions"


def session_scope(arguments: argparse.Namespace) -> JSONObject:
    """Bind local references and command receipts to both origins, tenant and TLS identities."""
    scope: JSONObject = {
        "controller": arguments.controller.rstrip("/"),
        "mission_service": arguments.mission_service.rstrip("/"),
        "tenant": arguments.tenant,
    }
    for name in ("ca", "client_cert", "mission_ca", "mission_client_cert"):
        path = getattr(arguments, name)
        scope[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
    return scope
