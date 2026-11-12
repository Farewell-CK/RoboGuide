"""Local first-run configuration with private files and environment-only model credentials."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from roboguide_workbench.client import ReadOnlyController, WorkbenchError, identifier
from roboguide_workbench.configuration import global_config_path, global_state_directory
from roboguide_workbench.provider_config import model_settings


def _toml_text(table: str, values: dict[str, str | bool | int]) -> str:
    """Encode bounded scalar settings without interpolating user text into TOML syntax."""
    lines = ["[" + table + "]"]
    for key, value in values.items():
        lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def _private_directory(path: Path) -> None:
    """Create only a private current-user settings directory without changing existing modes."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    status = path.stat()
    if path.is_symlink() or status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise WorkbenchError("初始化配置目录必须由当前用户持有、不是链接且权限为 0700。")


def _publish(path: Path, content: str) -> tuple[int, int]:
    """Publish a complete fsynced private file without overwriting any existing name."""
    descriptor, temporary = tempfile.mkstemp(prefix=".init-", dir=path.parent)
    linked = False
    identity: tuple[int, int] | None = None
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content.encode())
            output.flush()
            os.fsync(output.fileno())
            status = os.fstat(output.fileno())
            identity = status.st_dev, status.st_ino
        os.link(temporary, path)
        linked = True
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        status = path.lstat()
        if (status.st_dev, status.st_ino) != identity:
            raise WorkbenchError("初始化发布的配置身份发生变化；未替换现有文件。")
        return identity
    except BaseException:
        if linked and identity is not None:
            _remove_owned(path, identity)
        raise
    finally:
        os.unlink(temporary)


def _remove_owned(path: Path, identity: tuple[int, int]) -> None:
    """Remove only this publication's exact inode, retaining any replacement's original bytes."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    if (status.st_dev, status.st_ino) != identity:
        return
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_configuration(
    *,
    base_url: str,
    model: str,
    controller: str = "http://127.0.0.1:8080",
    mission_service: str = "http://127.0.0.1:8070",
    api_key_env: str = "OPENAI_API_KEY",
    requires_auth: bool = True,
    config_path: Path | None = None,
    tenant: str | None = None,
    ca: Path | None = None,
    client_cert: Path | None = None,
    client_key: Path | None = None,
    mission_ca: Path | None = None,
    mission_client_cert: Path | None = None,
    mission_client_key: Path | None = None,
) -> Path:
    """Save an independent reusable profile without copying credentials or changing a service."""
    path = (config_path or global_config_path()).expanduser().absolute()
    model_path = path.with_name("model.toml")
    if path == model_path:
        raise WorkbenchError("工作台配置文件不能与 model.toml 使用同一名称。")
    # Validate original URLs and complete remote TLS identity before publishing any settings.
    ReadOnlyController(controller, tenant=tenant, ca=ca, certificate=client_cert, key=client_key)
    ReadOnlyController(
        mission_service,
        tenant=tenant,
        ca=mission_ca,
        certificate=mission_client_cert,
        key=mission_client_key,
    )
    model_values: dict[str, str | bool | int] = {
        "base_url": base_url,
        "name": model,
        "responses_path": "/responses",
        "api_key_env": api_key_env,
        "requires_auth": requires_auth,
        "reasoning_effort": "medium",
        "timeout_seconds": 60,
    }
    model_settings(dict(model_values))
    profile: dict[str, str | bool | int] = {
        "controller": controller,
        "mission_service": mission_service,
        "config": "model.toml",
        "session_dir": str(global_state_directory() / "sessions"),
        "answer_mode": "grounded",
        "tool_limit": 4,
        "call_limit": 3,
        "turn_seconds": 60,
    }
    if tenant is not None:
        profile["tenant"] = identifier(tenant)
    for name, value in (
        ("ca", ca),
        ("client_cert", client_cert),
        ("client_key", client_key),
        ("mission_ca", mission_ca),
        ("mission_client_cert", mission_client_cert),
        ("mission_client_key", mission_client_key),
    ):
        if value is not None:
            profile[name] = str(value.expanduser().absolute())
    _private_directory(path.parent)
    if path.exists() or path.is_symlink() or model_path.exists() or model_path.is_symlink():
        raise WorkbenchError("已有配置文件，未覆盖；请使用现有配置或指定新的初始化目录。")
    published = _publish(model_path, _toml_text("model", model_values))
    try:
        _publish(path, _toml_text("workbench", profile))
    except BaseException:
        # A profile left behind by failed cleanup may still reference this model. Retain
        # that consistent pair; otherwise remove only our exact unreferenced model inode.
        if not path.exists() and not path.is_symlink():
            _remove_owned(model_path, published)
        raise
    return path


def interactive_setup(
    *,
    config_path: Path | None = None,
    input_text: Callable[[str], str] | None = None,
    emit: Callable[[str], None] = print,
    initial: Mapping[str, object] | None = None,
) -> Path:
    """Ask once for connection settings while never asking users to paste model credentials."""
    read = input_text or input
    settings = dict(initial or {})
    allowed = {
        "controller",
        "mission_service",
        "api_key_env",
        "requires_auth",
        "tenant",
        "ca",
        "client_cert",
        "client_key",
        "mission_ca",
        "mission_client_cert",
        "mission_client_key",
    }
    if set(settings) - allowed:
        raise WorkbenchError("首次配置含不支持的初始参数。")
    for key, value in settings.items():
        expected = (
            bool
            if key == "requires_auth"
            else Path
            if key
            in {
                "ca",
                "client_cert",
                "client_key",
                "mission_ca",
                "mission_client_cert",
                "mission_client_key",
            }
            else str
        )
        if not isinstance(value, expected):
            raise WorkbenchError("首次配置初始参数类型无效。")
    emit("首次配置 RoboGuide；仅保存本地连接设置，不启动服务或调用模型。")
    emit("API Key 从指定环境变量读取；本向导不会要求输入或保存密钥。")
    controller = (
        str(settings["controller"])
        if "controller" in settings
        else (read("Controller 地址 [http://127.0.0.1:8080]：").strip() or "http://127.0.0.1:8080")
    )
    mission = (
        str(settings["mission_service"])
        if "mission_service" in settings
        else (
            read("Mission Service 地址 [http://127.0.0.1:8070]：").strip()
            or "http://127.0.0.1:8070"
        )
    )
    endpoint = (
        read("模型服务 base URL [https://api.openai.com/v1]：").strip()
        or "https://api.openai.com/v1"
    )
    name = read("模型名称（必填）：").strip()
    key_env = (
        str(settings["api_key_env"])
        if "api_key_env" in settings
        else (read("API Key 环境变量名 [OPENAI_API_KEY]：").strip() or "OPENAI_API_KEY")
    )
    authenticate = (
        ("y" if settings["requires_auth"] else "n")
        if "requires_auth" in settings
        else (read("模型服务需要 API Key？[Y/n]：").strip().lower())
    )
    if authenticate not in {"", "y", "yes", "n", "no"}:
        raise WorkbenchError("认证选项请填写 y 或 n。")
    tls: dict[str, Path | None] = {
        key: value for key, value in settings.items() if isinstance(value, Path)
    }
    for prefix, label, endpoint_url in (
        ("", "Controller", controller),
        ("mission_", "Mission Service", mission),
    ):
        parsed = urlsplit(endpoint_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            for field, description in (
                ("ca", "CA 证书"),
                ("client_cert", "客户端证书"),
                ("client_key", "客户端私钥"),
            ):
                if prefix + field in tls:
                    continue
                raw = read(label + " " + description + "路径：").strip()
                tls[prefix + field] = Path(raw).expanduser() if raw else None
    path = write_configuration(
        base_url=endpoint,
        model=name,
        controller=controller,
        mission_service=mission,
        api_key_env=key_env,
        requires_auth=authenticate not in {"n", "no"},
        config_path=config_path,
        tenant=str(settings["tenant"]) if "tenant" in settings else None,
        ca=tls.get("ca"),
        client_cert=tls.get("client_cert"),
        client_key=tls.get("client_key"),
        mission_ca=tls.get("mission_ca"),
        mission_client_cert=tls.get("mission_client_cert"),
        mission_client_key=tls.get("mission_client_key"),
    )
    emit("配置已保存：" + str(path))
    emit("以后可在任意目录运行 roboguide chat；后端服务需已运行。")
    return path
