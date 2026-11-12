"""Standalone conversation model settings without deployment-specific Mission assets."""

from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from mission.config import LlmSettings, MissionSettings, ProviderSettings, load_settings

from roboguide_workbench.client import WorkbenchError

MODEL_FIELDS = {
    "base_url",
    "name",
    "responses_path",
    "api_key_env",
    "requires_auth",
    "reasoning_effort",
    "timeout_seconds",
}


@dataclass(frozen=True, slots=True)
class ChatSettings:
    """Contain only the model inputs consumed by the conversation router and evidence answerer."""

    provider: ProviderSettings
    llm: LlmSettings


def model_settings(model: dict[str, object]) -> ChatSettings:
    """Validate a closed model profile without requiring deployment assets or credentials."""
    if set(model) - MODEL_FIELDS or not {"base_url", "name"} <= set(model):
        raise WorkbenchError("模型配置必须提供 base_url/name，且不能包含未知字段。")
    values = {
        "base_url": model["base_url"],
        "name": model["name"],
        "responses_path": model.get("responses_path", "/responses"),
        "api_key_env": model.get("api_key_env", "OPENAI_API_KEY"),
        "reasoning_effort": model.get("reasoning_effort", "medium"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise WorkbenchError("模型配置文本字段不能为空。")
    base_url, name = str(values["base_url"]), str(values["name"])
    path, key_env = str(values["responses_path"]), str(values["api_key_env"])
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(char) < 32 for char in base_url)
        or urlsplit(path).scheme
        or urlsplit(path).netloc
        or "?" in path
        or "#" in path
        or any(ord(char) < 32 for char in path)
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env) is None
    ):
        raise WorkbenchError("模型地址、响应路径或凭据环境变量名称无效。")
    requires_auth, timeout = model.get("requires_auth", True), model.get("timeout_seconds", 60)
    if not isinstance(requires_auth, bool) or (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 600
    ):
        raise WorkbenchError("模型认证须为布尔值，超时须为 0–600 秒之间的有限数字。")
    provider = ProviderSettings(
        name="workbench",
        base_url=base_url,
        responses_path=path,
        wire_api="responses",
        requires_openai_auth=requires_auth,
        api_key_env=key_env,
    )
    provider.endpoint(os.environ)
    return ChatSettings(
        provider=provider,
        llm=LlmSettings(
            model_provider="workbench",
            model=name,
            review_model=name,
            reasoning_effort=str(values["reasoning_effort"]),
            disable_response_storage=True,
            network_access="enabled",
            timeout_seconds=float(timeout),
        ),
    )


def load_chat_settings(path: Path) -> MissionSettings | ChatSettings:
    """Read standalone model TOML or retain the existing full Mission configuration contract."""
    with path.open("rb") as source:
        raw = tomllib.load(source)
    if "model" not in raw:
        return load_settings(path, repository_root=path.resolve().parent.parent)
    if set(raw) != {"model"} or not isinstance(raw["model"], dict):
        raise WorkbenchError("独立对话模型配置必须只包含 [model]。")
    return model_settings(raw["model"])
