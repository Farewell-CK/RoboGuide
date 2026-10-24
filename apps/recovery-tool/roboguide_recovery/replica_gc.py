"""Verify Root7 original GC intents, irreversible tombstones and independent Unknown outcomes."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import replica_content as content
from .snapshot_references import OperationBudget


def _unsigned(value: Any, *, positive: bool = False) -> bool:
    """Reject bool, negative, overflow and missing original kernel observations."""
    return type(value) is int and (1 if positive else 0) <= value < 2**64


def validate(identity: dict[str, Any], tables: dict[str, Any], budget: OperationBudget) -> None:
    """Require original ownership and inactive references without sampling new effects."""
    commands = tables["content_gc_commands"]
    tombstones = tables["content_tombstones"]
    if len(commands) > 4096:
        raise ValueError("Replica original GC command history exceeds its bound")
    references: dict[tuple[str, str, int], dict[str, Any]] = defaultdict(dict)
    for key, record in tables["content_references"].items():
        references[content._content(record["reference"]["content"])][key] = record
    owned: set[str] = set()
    for operation, raw in commands.items():
        budget.check()
        record = content._closed(raw, {"intent", "outcomes"})
        intent = content._closed(
            record["intent"], {"schema", "command_id", "receiver", "budget", "items"}
        )
        content._budget(intent["budget"])
        command = intent["command_id"]
        if (
            intent["schema"] != "roboguide.replica-content-gc/v0.1"
            or not isinstance(command, str)
            or not 0 < len(command.encode()) <= 128
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in command)
            or content._digest(command) != operation
            or intent["receiver"] != identity
            or not isinstance(intent["items"], list)
            or len(intent["items"]) > 16384
            or not isinstance(record["outcomes"], list)
            or len(record["outcomes"]) != len(intent["items"])
        ):
            raise ValueError("Replica original GC command identity/items/budget differ")
        previous = None
        intent_digest = content._digest(intent)
        for raw_item, raw_outcome in zip(intent["items"], record["outcomes"], strict=True):
            budget.check()
            item = content._closed(
                raw_item, {"receipt", "reference_digest", "file", "prior_tombstone"}
            )
            receipt = item["receipt"]
            key = content._content(receipt["content"])
            if previous is not None and previous >= key:
                raise ValueError("Replica GC original qualified selection is not strictly ordered")
            previous = key
            table = (
                "content_streams"
                if receipt["schema"] == "roboguide.replica-content-stream-receipt/v0.1"
                else "content_operations"
            )
            original = tables[table].get(content._digest(receipt["command_id"]))
            if (
                original is None
                or original["receipt"] != receipt
                or receipt["store_owner"] != identity["store_owner"]
                or receipt["member"] != identity["member"]
                or receipt["profile_identity"] != identity["profile_identity"]
                or any(value["active"] for value in references[key].values())
                or item["reference_digest"] != content._digest(references[key])
            ):
                raise ValueError(
                    "Replica GC original receipt or complete inactive references differ"
                )
            if item["file"] is not None:
                file = content._closed(item["file"], {"device", "inode"})
                if not _unsigned(file["device"]) or not _unsigned(file["inode"], positive=True):
                    raise ValueError("Replica GC original file observation differs")
            if raw_outcome is not None:
                outcome = content._closed(raw_outcome, {"state", "observed_ms"})
                if (
                    outcome["state"] not in {"deleted", "missing"}
                    or not _unsigned(outcome["observed_ms"])
                    or not content._completed(intent["budget"], outcome["observed_ms"])
                    or (outcome["state"] == "deleted" and item["file"] is None)
                ):
                    raise ValueError("Replica GC outcome lacks its original effect evidence")
            tombstone_key = content._digest(receipt["content"])
            if tombstone_key not in tombstones:
                raise ValueError("Replica GC original command lost its mandatory tombstone")
            fence = content._closed(
                tombstones[tombstone_key], {"content", "command_id", "intent_digest"}
            )
            if fence["content"] != receipt["content"]:
                raise ValueError("Replica GC tombstone changed qualified ownership")
            if item["prior_tombstone"] is None:
                if fence["command_id"] != command or fence["intent_digest"] != intent_digest:
                    raise ValueError("Replica GC tombstone changed its original first command")
                owned.add(tombstone_key)
            elif item["prior_tombstone"] != fence["command_id"] or fence["command_id"] == command:
                raise ValueError("Replica GC follow-up replaced its first tombstone owner")
    if owned != set(tombstones):
        raise ValueError("Replica GC tombstone inventory lost original command coverage")
