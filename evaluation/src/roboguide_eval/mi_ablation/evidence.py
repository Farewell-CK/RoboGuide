"""Immutable local evidence and real production-transport recording for MI ablation."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from mission.models import JSONObject
from mission.responses import JsonTransport


def digest(value: object) -> str:
    """Hash canonical JSON, without interpreting it as semantic correctness."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def write_new(path: Path, value: object) -> None:
    """Write a new bounded JSON witness; refuse replacement of an existing record."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(raw.encode()) > 8 * 1024 * 1024:
        raise ValueError("ablation evidence exceeds its byte bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_object(path: Path) -> JSONObject:
    """Read one bounded JSON object without accepting oversized evidence."""
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("ablation input exceeds its byte bound")
    value: object = json.loads(raw)
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("ablation input must be a JSON object")
    return cast(JSONObject, value)


class RecordingTransport:
    """Record exact sanitized bodies before parsing, with no implicit retry or hidden calls."""

    def __init__(
        self, delegate: JsonTransport, directory: Path, model: str, secrets: tuple[str, ...]
    ) -> None:
        """Bind a finite call ledger to an isolated trial and its authorized model."""
        self.delegate = delegate
        self.directory = directory
        self.model = model
        self.secrets = tuple(value for value in secrets if value)
        self.stage = "unassigned"
        self.calls: list[JSONObject] = []
        self.limit = 12

    def _safe(self, value: object) -> object:
        """Redact known credentials without recording headers, prefixes or credential hashes."""
        text = json.dumps(value, ensure_ascii=False)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return json.loads(text)

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Persist the actual envelope, make one call and retain failures before propagation."""
        if self.stage == "unassigned" or len(self.calls) >= self.limit:
            raise ValueError("unassigned stage or exhausted ablation call budget")
        if payload.get("model") != self.model:
            raise ValueError("ablation requested model differs from frozen configuration")
        index = len(self.calls) + 1
        directory = self.directory / f"call-{index:02d}"
        write_new(
            directory / "request.json",
            self._safe({"url": url, "timeout_seconds": timeout_seconds, "payload": payload}),
        )
        started = time.monotonic()
        entry: JSONObject = {"index": index, "stage": self.stage, "started_unix": time.time()}
        self.calls.append(entry)
        try:
            response = self.delegate.post_json(url, headers, payload, timeout_seconds)
            write_new(directory / "response.json", self._safe(response))
            safe_response = cast(JSONObject, self._safe(response))
            entry.update(
                response_model=safe_response.get("model"), usage=safe_response.get("usage")
            )
            if response.get("model") != self.model:
                raise ValueError("ablation response model identity is unavailable or different")
            entry["status"] = "response_received"
            return response
        except Exception as error:
            entry.update(status="error", error_type=type(error).__name__)
            write_new(
                directory / "error.json",
                self._safe({"error_type": type(error).__name__, "message": str(error)}),
            )
            raise
        finally:
            entry["elapsed_seconds"] = time.monotonic() - started
            write_new(directory / "metadata.json", self._safe(entry))
