"""In-memory agent registry for the multi-agent control UI.

The legacy client was single-agent: a single ``ClientSettings`` is
collected from the connection form and every API call passes the same
``settings`` blob. The new UI lets the user register multiple robot
dogs (each with its own host/port/user-id) and dispatch tasks to all
of them through a single chat surface.

This module owns the in-memory mapping ``agent_id -> ClientSettings``
and the persisted copy on disk (same ``settings.yaml`` as the legacy
fields, so a user upgrading keeps every value). It also exposes a
``resolve_settings`` helper that the existing single-agent endpoints
can use to look up the right ``ClientSettings`` from an incoming
``agent_id`` (or fall back to the default agent when the front end
hasn't been migrated yet).
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .transport import ClientSettings

DEFAULT_AGENT_ID = "default"
_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _validate_agent_id(agent_id: str) -> str:
    """Normalize and validate an agent id.

    Empty strings collapse to ``DEFAULT_AGENT_ID`` so the legacy front
    end (which never had the concept of an id) keeps working without
    any changes. Anything else must match ``_AGENT_ID_PATTERN``; this
    is later used as a URL path component so the restriction is the
    same one FastAPI/Starlette already enforce, with a few more
    characters banned to avoid shell-injection-like surprises.
    """
    candidate = (agent_id or "").strip() or DEFAULT_AGENT_ID
    if not _AGENT_ID_PATTERN.fullmatch(candidate):
        raise ValueError(
            "agent_id must match [A-Za-z0-9_.-]{1,64} (got %r)" % agent_id
        )
    return candidate


class AgentRecord:
    """A single registered agent.

    ``settings`` is the on-the-wire ``ClientSettings`` payload. The
    registry always stores a fully-populated record (no ``None``) so
    callers don't need to handle partial state. ``label`` is the
    human-readable name shown in the sidebar; it falls back to
    ``robot_host`` when the user hasn't customised it.
    """

    __slots__ = ("agent_id", "label", "settings", "created_at", "last_seen")

    def __init__(
        self,
        agent_id: str,
        label: str,
        settings: ClientSettings,
        created_at: float | None = None,
        last_seen: float | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.label = label or settings.robot_host or agent_id
        self.settings = settings
        self.created_at = float(created_at) if created_at else time.time()
        self.last_seen = float(last_seen) if last_seen else self.created_at

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "label": self.label,
            "host": self.settings.robot_host,
            "atlasEndpoint": self.settings.atlas_endpoint,
            "userId": self.settings.user_id,
            "createdAt": self.created_at,
            "lastSeen": self.last_seen,
        }


class AgentRegistry:
    """Process-wide registry of agents.

    Designed to be a singleton (``agents.registry`` below) but
    implemented as a class so tests can instantiate an isolated
    registry. The persisted copy lives next to the legacy
    ``settings.yaml`` under a top-level ``agents:`` key, so an
    existing single-agent install keeps its connection details after
    upgrade and the user is just shown one default entry.
    """

    def __init__(self) -> None:
        self._records: dict[str, AgentRecord] = {}
        # Legacy single-agent settings; used to seed ``default`` on
        # the first run so the upgrade path is transparent.
        self._legacy: dict[str, Any] = {}

    # ── Persistence ─────────────────────────────────────────────────
    def hydrate(self, legacy: dict[str, Any], persisted_agents: list[dict[str, Any]]) -> None:
        """Populate from the legacy settings blob + a list of agents.

        The legacy blob is only used to seed the ``default`` agent
        when no persisted entry exists for it; this is the upgrade
        path. Subsequent writes to the legacy fields keep updating
        the default agent's settings (handled by ``update_legacy``).
        """
        self._legacy = dict(legacy)
        for raw in persisted_agents:
            if not isinstance(raw, dict):
                continue
            agent_id = _validate_agent_id(raw.get("agentId", ""))
            settings_payload = raw.get("settings") or {}
            if not isinstance(settings_payload, dict):
                continue
            try:
                settings = ClientSettings.from_payload(settings_payload)
            except Exception:
                # Drop the corrupt entry so a bad upgrade doesn't
                # wedge the registry; the user can re-create the
                # agent from the UI.
                continue
            label = (raw.get("label") or "").strip() or settings.robot_host or agent_id
            self._records[agent_id] = AgentRecord(
                agent_id=agent_id,
                label=label,
                settings=settings,
                created_at=raw.get("createdAt"),
                last_seen=raw.get("lastSeen"),
            )
        if DEFAULT_AGENT_ID not in self._records and self._legacy:
            try:
                settings = ClientSettings.from_payload(self._legacy)
                self._records[DEFAULT_AGENT_ID] = AgentRecord(
                    agent_id=DEFAULT_AGENT_ID,
                    label=settings.robot_host or DEFAULT_AGENT_ID,
                    settings=settings,
                )
            except Exception:
                pass

    def update_legacy(self, settings: dict[str, Any]) -> None:
        """Mirror a legacy ``/api/settings`` write into ``default``."""
        self._legacy = dict(settings)
        try:
            parsed = ClientSettings.from_payload(settings)
        except Exception:
            return
        existing = self._records.get(DEFAULT_AGENT_ID)
        if existing is None:
            self._records[DEFAULT_AGENT_ID] = AgentRecord(
                agent_id=DEFAULT_AGENT_ID,
                label=parsed.robot_host or DEFAULT_AGENT_ID,
                settings=parsed,
            )
        else:
            existing.settings = parsed
            existing.last_seen = time.time()

    def persisted_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "agentId": rec.agent_id,
                "label": rec.label,
                "settings": rec.settings.to_payload(),
                "createdAt": rec.created_at,
                "lastSeen": rec.last_seen,
            }
            for rec in self._records.values()
        ]

    # ── CRUD ────────────────────────────────────────────────────────
    def list(self) -> list[AgentRecord]:
        return list(self._records.values())

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._records.get(_validate_agent_id(agent_id))

    def upsert(
        self,
        agent_id: str,
        label: str,
        settings_payload: dict[str, Any],
    ) -> AgentRecord:
        agent_id = _validate_agent_id(agent_id)
        if agent_id == DEFAULT_AGENT_ID:
            # Default agent is special: its label is just the host
            # (the legacy front end never had a name field), so we
            # ignore any user-supplied label rather than letting it
            # desync.
            label = ""
        parsed = ClientSettings.from_payload(settings_payload)
        existing = self._records.get(agent_id)
        if existing is None:
            record = AgentRecord(
                agent_id=agent_id,
                label=label or parsed.robot_host or agent_id,
                settings=parsed,
            )
            self._records[agent_id] = record
        else:
            existing.settings = parsed
            existing.last_seen = time.time()
            if label and agent_id != DEFAULT_AGENT_ID:
                existing.label = label
            elif agent_id == DEFAULT_AGENT_ID:
                existing.label = parsed.robot_host or agent_id
        return self._records[agent_id]

    def rename(self, agent_id: str, label: str) -> AgentRecord | None:
        agent_id = _validate_agent_id(agent_id)
        rec = self._records.get(agent_id)
        if rec is None:
            return None
        cleaned = (label or "").strip()
        if not cleaned or agent_id == DEFAULT_AGENT_ID:
            rec.label = rec.settings.robot_host or rec.agent_id
        else:
            rec.label = cleaned[:64]
        return rec

    def remove(self, agent_id: str) -> bool:
        agent_id = _validate_agent_id(agent_id)
        return self._records.pop(agent_id, None) is not None

    def resolve_settings(self, agent_id: str | None) -> ClientSettings:
        """Look up the ``ClientSettings`` for an incoming request.

        When ``agent_id`` is missing, empty, or unknown we fall back
        to ``default`` so the legacy single-agent front end keeps
        working without any migration. The caller (``app.py``) is
        expected to treat this as a "use whatever is current" hint,
        not as a strict resolution: if even the default agent is
        missing we surface ``default`` and the transport layer will
        raise with the real reason (bad host, no atlas, etc.).
        """
        if agent_id:
            try:
                rec = self.get(agent_id)
            except ValueError:
                rec = None
            if rec is not None:
                rec.last_seen = time.time()
                return rec.settings
        if DEFAULT_AGENT_ID in self._records:
            rec = self._records[DEFAULT_AGENT_ID]
            rec.last_seen = time.time()
            return rec.settings
        # Cold start with no persisted agents and no legacy seed
        # — synthesise a default from the environment so the very
        # first request doesn't 500 before the UI has had a chance
        # to register anything.
        env_host = os.environ.get("ROBONIX_ROBOT_HOST", "127.0.0.1")
        env_port = int(os.environ.get("ROBONIX_ATLAS_PORT", "50051"))
        return ClientSettings.from_payload(
            {"robotHost": env_host, "atlasPort": env_port}
        )


# ── Process singleton ────────────────────────────────────────────────
registry = AgentRegistry()


# ── Settings file I/O (agents + legacy) ──────────────────────────────
def _settings_path() -> Path:
    return Path(
        os.environ.get(
            "ROBONIX_CLIENT_SETTINGS",
            Path.home() / ".config" / "robonix-client" / "settings.yaml",
        )
    ).expanduser()


PERSISTED_LEGACY_KEYS = {
    "robotHost", "atlasPort", "liaisonEndpoint", "userId", "recordSeconds",
    "language", "micNodeId", "micDeviceId", "speakerNodeId",
    "speakerDeviceId", "ttsNodeId", "enrollUserId", "enrollUserName",
}


def _load_file(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.exists():
        return {}, []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"settings file must contain a mapping: {path}")
    legacy = {k: raw[k] for k in PERSISTED_LEGACY_KEYS if k in raw}
    agents_raw = raw.get("agents")
    agents_list: list[dict[str, Any]] = []
    if isinstance(agents_raw, list):
        for entry in agents_raw:
            if isinstance(entry, dict):
                agents_list.append(entry)
    return legacy, agents_list


def _save_file(
    path: Path,
    legacy: dict[str, Any],
    agents_list: list[dict[str, Any]],
) -> None:
    payload: dict[str, Any] = {
        k: legacy[k] for k in PERSISTED_LEGACY_KEYS if k in legacy
    }
    payload["agents"] = agents_list
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def hydrate_from_disk() -> Path:
    """Load the registry from disk. Returns the path used."""
    path = _settings_path()
    legacy, persisted_agents = _load_file(path)
    registry.hydrate(legacy, persisted_agents)
    return path


def persist_to_disk() -> Path:
    """Write the current registry (legacy + agents) to disk."""
    path = _settings_path()
    _save_file(path, registry._legacy, registry.persisted_agents())  # noqa: SLF001
    return path


def new_agent_id() -> str:
    """Generate a short random id for an unregistered agent."""
    return f"agent-{uuid.uuid4().hex[:8]}"
