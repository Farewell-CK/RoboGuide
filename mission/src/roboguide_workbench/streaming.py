"""Bounded Responses SSE decoding; only the completed response may authorize tool calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from http.client import HTTPResponse

from mission.models import JSONObject

from roboguide_workbench.client import (
    MAX_RESPONSE_BYTES,
    WorkbenchError,
    decode_object,
    request_json,
)


def consume(response: HTTPResponse, emit: Callable[[str], None]) -> JSONObject:
    """Decode bounded events under the HTTP reader's original deadline without any retry."""
    streaming = (
        response.getheader("Content-Type", "").split(";", 1)[0].strip() == "text/event-stream"
    )
    raw = bytearray()
    pending = bytearray()
    lines: list[bytes] = []
    completed: JSONObject | None = None
    preview_size = 0
    preview: list[str] = []

    def event() -> None:
        """Accept only complete JSON data frames and never render reasoning or arguments."""
        nonlocal completed, preview_size
        if not lines:
            return
        data = b"\n".join(lines)
        lines.clear()
        if data == b"[DONE]":
            return
        item = decode_object(data)
        kind = item.get("type")
        if kind in {"error", "response.failed", "response.incomplete"}:
            raise WorkbenchError("模型流未完成；预览不作为最终回复或工具请求。")
        if kind == "response.output_text.delta":
            delta = item.get("delta")
            if not isinstance(delta, str) or completed is not None:
                raise WorkbenchError("模型文本流格式无效。")
            preview_size += len(delta.encode())
            if preview_size > 64_000:
                raise WorkbenchError("模型流超过文本显示上限。")
            emit(delta)
            preview.append(delta)
        elif kind == "response.completed":
            value = item.get("response")
            if completed is not None or not isinstance(value, dict):
                raise WorkbenchError("模型流完成证据重复或无效。")
            completed = value

    while not response.isclosed():
        chunk = response.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(raw)))
        raw.extend(chunk)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WorkbenchError("模型流超过 2 MiB。")
        if not chunk:
            break
        if streaming:
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, tail = pending.partition(b"\n")
                pending[:] = tail
                line = line.rstrip(b"\r")
                if not line:
                    event()
                elif line.startswith(b"data:"):
                    lines.append(bytes(line[5:]).lstrip(b" "))
    if not streaming:
        return decode_object(raw)
    if pending or lines or completed is None:
        raise WorkbenchError("模型流结束但未取得完整完成证据；未采用任何工具请求。")
    texts: list[str] = []
    output = completed.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str):
                        texts.append(text)
    final_text = "".join(texts)
    if preview and "".join(preview) != final_text:
        raise WorkbenchError("模型流文本与完成证据不一致，预览未确认为最终回复。")
    return completed


def post_stream(
    url: str,
    headers: Mapping[str, str],
    payload: JSONObject,
    seconds: float,
    emit: Callable[[str], None],
) -> JSONObject:
    """Use one existing fixed-origin POST with streaming enabled and original framing checks."""
    body = {**payload, "stream": True}
    return request_json(
        url,
        "POST",
        {**headers, "Accept": "text/event-stream"},
        json.dumps(body, ensure_ascii=False).encode(),
        seconds,
        consume=lambda response: consume(response, emit),
    )
