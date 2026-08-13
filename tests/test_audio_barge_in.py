from __future__ import annotations

import asyncio
import json

from robonix_client.audio_device_server import server_web


def test_pcm_peak_level_tracks_s16_output() -> None:
    assert server_web._pcm_peak_level(b"") == 0.0
    assert server_web._pcm_peak_level(b"\x00\x00\x00\x40") == 0.5
    assert server_web._pcm_peak_level(b"\x00\x80") == 1.0


def test_speaker_stop_discards_buffer_without_drain(monkeypatch) -> None:
    lifecycle: list[str] = []

    class FakeStream:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            lifecycle.append("start")

        def stop(self) -> None:
            lifecycle.append("stop")

        def abort(self) -> None:
            lifecycle.append("abort")

        def close(self) -> None:
            lifecycle.append("close")

    class FakeWebSocket:
        remote_address = ("127.0.0.1", 1)

        def __init__(self) -> None:
            self._messages = iter(
                [b"queued speech that must not finish", json.dumps({"type": "stop"})]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    monkeypatch.setattr(server_web.sd, "RawOutputStream", FakeStream)
    monkeypatch.setattr(server_web, "pick_output_device", lambda _explicit: None)

    asyncio.run(asyncio.wait_for(server_web.serve_speaker(FakeWebSocket()), timeout=0.5))

    assert lifecycle == ["start", "abort", "close"]


def test_speaker_eof_clears_level_after_output_drain(monkeypatch) -> None:
    lifecycle: list[str] = []
    original_set_state = server_web._set_state

    class FakeStream:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            lifecycle.append("start")

        def stop(self) -> None:
            lifecycle.append("stop")

        def close(self) -> None:
            lifecycle.append("close")

    class FakeWebSocket:
        remote_address = ("127.0.0.1", 1)

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    def record_set_state(key, value) -> None:
        if key == "output_level":
            lifecycle.append(f"level:{value}")
        original_set_state(key, value)

    monkeypatch.setattr(server_web.sd, "RawOutputStream", FakeStream)
    monkeypatch.setattr(server_web, "pick_output_device", lambda _explicit: None)
    monkeypatch.setattr(server_web, "_set_state", record_set_state)

    asyncio.run(asyncio.wait_for(server_web.serve_speaker(FakeWebSocket()), timeout=0.5))

    assert lifecycle == ["level:0.0", "start", "stop", "level:0.0", "close"]


def test_serve_vu_does_not_block_event_loop_on_slow_restart(monkeypatch) -> None:
    """A slow/hung native device open in VuMonitor.restart() (observed: an
    indefinite hang opening a stale CoreAudio device after macOS sleep/wake)
    must not freeze the shared event loop that also serves /mic and
    /speaker -- restart() has to run off-thread."""
    import time as time_module

    class SlowVuMonitor:
        def __init__(self) -> None:
            self.level = 0.0

        def restart(self, _device) -> None:
            time_module.sleep(0.3)

    class FakeWs:
        remote_address = ("127.0.0.1", 1)

        async def send(self, _msg) -> None:
            return None

    monkeypatch.setattr(server_web, "vu_monitor", SlowVuMonitor())
    monkeypatch.setattr(server_web, "_state", lambda _key: 7)

    ticks = {"n": 0}

    async def ticker():
        while True:
            ticks["n"] += 1
            await asyncio.sleep(0.02)

    async def run():
        ticker_task = asyncio.create_task(ticker())
        vu_task = asyncio.create_task(server_web.serve_vu(FakeWs()))
        await asyncio.sleep(0.35)
        for task in (vu_task, ticker_task):
            task.cancel()
        for task in (vu_task, ticker_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())

    # A frozen event loop would leave ticks near 0 for the ~0.3s restart()
    # blocks; off-thread execution lets the ticker keep running concurrently.
    assert ticks["n"] > 5
