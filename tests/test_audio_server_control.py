from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from robonix_client import audio_server_control


def _reset_audio_process() -> None:
    if audio_server_control._log_handle is not None:
        audio_server_control._log_handle.close()
    audio_server_control._process = None
    audio_server_control._log_handle = None


def test_health_bypasses_environment_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSocket:
        async def recv(self) -> str:
            return json.dumps({"ok": True})

    class FakeConnection:
        async def __aenter__(self) -> FakeSocket:
            return FakeSocket()

        async def __aexit__(self, *_args) -> None:
            return None

    def fake_connect(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeConnection()

    monkeypatch.setattr(audio_server_control.websockets, "connect", fake_connect)

    result = asyncio.run(audio_server_control.health("127.0.0.1", 60000))

    assert result["reachable"] is True
    assert captured["url"] == "ws://127.0.0.1:60000/health"
    assert captured["proxy"] is None


def test_start_supports_linux_portaudio(monkeypatch, tmp_path) -> None:
    _reset_audio_process()
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(audio_server_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(audio_server_control.importlib, "import_module", lambda _name: object())
    monkeypatch.setattr(audio_server_control, "_port_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(audio_server_control.subprocess, "Popen", fake_popen)

    result = audio_server_control.start(port=60100)

    assert result["ok"] is True
    assert result["platform"] == "Linux"
    assert result["backend"] == "PortAudio/Linux"
    assert result["pid"] == 1234
    assert captured["cmd"][0] == audio_server_control.sys.executable
    assert "server_web.py" in captured["cmd"][1]
    assert captured["cmd"][-2:] == ["--ui-host", audio_server_control.DEFAULT_UI_HOST]
    _reset_audio_process()


def test_start_resets_stale_unresponsive_listener(monkeypatch, tmp_path) -> None:
    """A dead server left on the port (e.g. an unclean prior exit) must be
    force-reset and replaced, not just reported as an error."""
    _reset_audio_process()
    killed: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

    port_open_calls = {"n": 0}

    def fake_port_open(*_args, **_kwargs):
        # open (stale server) until the reset kills it, then closed.
        port_open_calls["n"] += 1
        return port_open_calls["n"] <= 2

    async def fake_health(*_args, **_kwargs):
        return {"reachable": False, "error": "health timeout"}

    monkeypatch.setattr(audio_server_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(audio_server_control.importlib, "import_module", lambda _name: object())
    monkeypatch.setattr(audio_server_control, "_port_open", fake_port_open)
    monkeypatch.setattr(audio_server_control, "health", fake_health)
    monkeypatch.setattr(audio_server_control, "_pids_on_port", lambda _port: [9999])
    monkeypatch.setattr(audio_server_control.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(audio_server_control.time, "sleep", lambda _s: None)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(audio_server_control.subprocess, "Popen", lambda *_a, **_k: FakeProcess())

    result = audio_server_control.start(port=60101)

    assert killed and killed[0] == (9999, audio_server_control.signal.SIGTERM)
    assert result["ok"] is True
    assert result["pid"] == 4321
    _reset_audio_process()


def test_start_reports_unresettable_stale_listener(monkeypatch) -> None:
    """If the stale listener can't be reset (no pid found, e.g. unsupported
    platform), still fail loudly instead of silently spawning a duplicate."""
    _reset_audio_process()

    async def fake_health(*_args, **_kwargs):
        return {"reachable": False, "error": "health timeout"}

    monkeypatch.setattr(audio_server_control, "_port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(audio_server_control, "health", fake_health)
    monkeypatch.setattr(audio_server_control, "_pids_on_port", lambda _port: [])

    result = audio_server_control.start(port=60102)

    assert result["ok"] is False
    assert "could not be reset automatically" in result["error"]


def test_start_reports_linux_audio_dependency(monkeypatch) -> None:
    _reset_audio_process()
    monkeypatch.setattr(audio_server_control.platform, "system", lambda: "Linux")
    def missing_sounddevice(_name):
        raise ModuleNotFoundError("No module named 'sounddevice'")

    monkeypatch.setattr(audio_server_control.importlib, "import_module", missing_sounddevice)
    monkeypatch.setattr(audio_server_control, "_port_open", lambda *_args, **_kwargs: False)

    result = audio_server_control.start()

    assert result["ok"] is False
    assert "sounddevice" in result["error"]
    assert "apt install" in result["error"]


def test_start_supports_windows_portaudio(monkeypatch, tmp_path) -> None:
    _reset_audio_process()
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 5678

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(audio_server_control.platform, "system", lambda: "Windows")
    monkeypatch.setattr(audio_server_control.importlib, "import_module", lambda _name: object())
    monkeypatch.setattr(audio_server_control, "_port_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(audio_server_control.subprocess, "Popen", fake_popen)

    result = audio_server_control.start(port=60200)

    assert result["ok"] is True
    assert result["platform"] == "Windows"
    assert result["backend"] == "PortAudio/WASAPI"
    assert result["pid"] == 5678
    assert captured["cmd"][0] == audio_server_control.sys.executable
    assert "server_web.py" in captured["cmd"][1]
    assert captured["cmd"][-2:] == ["--ui-host", audio_server_control.DEFAULT_UI_HOST]
    _reset_audio_process()


def test_start_rejects_unsupported_local_audio_platform(monkeypatch) -> None:
    _reset_audio_process()
    monkeypatch.setattr(audio_server_control.platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(audio_server_control, "_port_open", lambda *_args, **_kwargs: False)

    result = audio_server_control.start()

    assert result["ok"] is False
    assert result["backend"] == "unsupported"
    assert "Linux, macOS, and Windows" in result["error"]


def test_stop_terminates_then_kills_and_reaps_owned_process(monkeypatch) -> None:
    _reset_audio_process()
    events: list[object] = []

    class FakeProcess:
        def poll(self):
            events.append("poll")
            return None

        def terminate(self) -> None:
            events.append("terminate")

        def wait(self, timeout: float) -> None:
            events.append(("wait", timeout))
            if events.count(("wait", timeout)) == 1:
                raise subprocess.TimeoutExpired(cmd="audio-device-server", timeout=timeout)

        def kill(self) -> None:
            events.append("kill")

    class FakeLogHandle:
        def close(self) -> None:
            events.append("close")

    process = FakeProcess()
    log_handle = FakeLogHandle()
    monkeypatch.setattr(audio_server_control, "_process", process)
    monkeypatch.setattr(audio_server_control, "_log_handle", log_handle)

    result = audio_server_control.stop()

    assert result == {"ok": True, "running": False}
    assert events == [
        "poll",
        "terminate",
        ("wait", 5),
        "kill",
        ("wait", 5),
        "close",
    ]
    assert audio_server_control._process is None
    assert audio_server_control._log_handle is None


def test_stop_is_harmless_for_external_audio_server(monkeypatch) -> None:
    _reset_audio_process()
    monkeypatch.setattr(audio_server_control, "_port_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        audio_server_control,
        "_blocking_health",
        lambda *_args, **_kwargs: {"reachable": True},
    )

    result = audio_server_control.start(port=60100)

    assert result["ok"] is True
    assert result["external"] is True
    assert audio_server_control._process is None
    assert audio_server_control.stop() == {"ok": True, "running": False}
    assert audio_server_control._process is None
