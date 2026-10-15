"""Semantic Memory requirements with no physical inventory or consumer selection authority."""

from __future__ import annotations

from dataclasses import dataclass

from mission.contract_values import (
    MAP_ID_PATTERN,
    JSONObject,
    JSONValue,
    MissionPlanError,
    _exact_keys,
    _object,
    _text,
)


@dataclass(frozen=True, slots=True)
class MemoryRequirement:
    """Require one immutable admitted selector and content contract before Task execution."""

    memory_id: str
    revision_id: str
    kind: str
    payload_schema: str
    media_type: str

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MemoryRequirement:
        """Reject consumer identity, paths, wildcards and unsupported semantic contracts."""
        item = _object(value, path)
        _exact_keys(item, {"selector", "kind", "payload_schema", "media_type"}, path)
        selector = _object(item["selector"], f"{path}.selector")
        _exact_keys(selector, {"memory_id", "revision_id"}, f"{path}.selector")
        memory = _text(selector["memory_id"], f"{path}.selector.memory_id")
        revision = _text(selector["revision_id"], f"{path}.selector.revision_id")
        if not all(MAP_ID_PATTERN.fullmatch(text) for text in (memory, revision)):
            raise MissionPlanError(f"{path}.selector must identify an immutable path-safe revision")
        kind = _text(item["kind"], f"{path}.kind")
        if kind not in {"execution", "spatial", "semantic", "experience", "artifact"}:
            raise MissionPlanError(f"{path}.kind is unsupported")
        payload = _text(item["payload_schema"], f"{path}.payload_schema")
        media = _text(item["media_type"], f"{path}.media_type")
        for text in (payload, media):
            if (
                text != text.strip()
                or len(text.encode()) > 256
                or "*" in text
                or any(ord(char) < 32 or ord(char) == 127 for char in text)
            ):
                raise MissionPlanError(f"{path} requires bounded exact content contracts")
        return cls(memory, revision, kind, payload, media)

    def to_json(self) -> JSONObject:
        """Serialize only accepted semantic requirements, independently of live deployment state."""
        return {
            "selector": {"memory_id": self.memory_id, "revision_id": self.revision_id},
            "kind": self.kind,
            "payload_schema": self.payload_schema,
            "media_type": self.media_type,
        }
