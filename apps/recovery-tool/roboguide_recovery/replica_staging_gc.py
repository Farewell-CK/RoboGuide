"""Verify Root8 original private-name cleanup without inferring current or historical CAS use."""

from __future__ import annotations

from typing import Any

from . import replica_content as content
from .replica_gc import _unsigned
from .snapshot_references import OperationBudget


def _command(value: Any) -> str:
    """Match bounded original IDs without treating their text as a filesystem component."""
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 128:
        raise ValueError("Replica staging cleanup original command is invalid")
    return value


def _ownership(tables: dict[str, Any], target: Any) -> tuple[tuple[str, str], dict[str, Any]]:
    """Derive exact original source and release from the independently verified provider journal."""
    target = content._closed(target, {"kind", "command_id"})
    command = _command(target["command_id"])
    kind = target["kind"]
    if kind not in {"copy", "stream"}:
        raise ValueError("Replica staging cleanup original selector kind differs")
    operation = content._digest(command)
    table = "content_operations" if kind == "copy" else "content_streams"
    original = tables[table].get(operation)
    if original is None:
        raise ValueError("Replica staging cleanup original source is missing")
    intent = original["intent"]
    pin_kind = "receive" if kind == "copy" else "stream"
    pin = tables["content_references"].get(pin_kind + ":" + operation)
    if pin is None or pin["active"] or pin["release_digest"] is None:
        raise ValueError("Replica staging cleanup original source is not released")
    content._pin(
        tables,
        pin_kind + ":" + operation,
        pin_kind,
        intent["content"],
        intent["authority_digest"] if kind == "copy" else intent["target"]["batch_digest"],
        active=False,
    )
    if kind == "copy":
        if original["receipt"] is None and original["process_end"] is None:
            raise ValueError("Replica staging cleanup original copy has no completion/end")
        source = {
            "intent": intent,
            "receipt": original["receipt"],
            "process_end": original["process_end"] if original["receipt"] is None else None,
        }
        leaf = operation
    else:
        retired = tables["content_stream_retirements"].get(operation)
        if (
            retired is None
            or retired["original"] != original
            or pin["release_digest"] != content._digest(retired)
        ):
            raise ValueError("Replica staging cleanup original stream retirement differs")
        source = original
        leaf = "stream-" + operation
    return (kind, command), {
        "content": intent["content"],
        "source_digest": content._digest(source),
        "release_digest": pin["release_digest"],
        "leaf": leaf,
    }


def validate(identity: dict[str, Any], tables: dict[str, Any], budget: OperationBudget) -> None:
    """Require complete original intent/item coverage, retirement and nonrenewed outcomes."""
    commands, items = tables["content_staging_gc_commands"], tables["content_staging_gc_items"]
    if len(commands) > 4096 or len(items) > 16384:
        raise ValueError("Replica staging cleanup original history exceeds its bound")
    owned: set[str] = set()
    for operation, raw in commands.items():
        budget.check()
        intent = content._closed(raw, {"schema", "command_id", "receiver", "budget", "items"})
        command = _command(intent["command_id"])
        content._budget(intent["budget"])
        selected = intent["items"]
        if (
            intent["schema"] != "roboguide.replica-staging-gc-intent/v0.1"
            or content._digest(command) != operation
            or intent["receiver"] != identity
            or not isinstance(selected, list)
            or not 1 <= len(selected) <= 64
        ):
            raise ValueError("Replica staging cleanup original intent/identity differs")
        previous = None
        for ordinal, raw_item in enumerate(selected):
            budget.check()
            item = content._closed(raw_item, {"target", "ownership", "file"})
            key, ownership = _ownership(tables, item["target"])
            if item["ownership"] != ownership or (previous is not None and previous >= key):
                raise ValueError("Replica staging cleanup original selection/lineage differs")
            previous = key
            original_file = item["file"]
            if original_file is not None:
                original_file = content._closed(original_file, {"device", "inode", "byte_size"})
                if (
                    not _unsigned(original_file["device"])
                    or not _unsigned(original_file["inode"], positive=True)
                    or not _unsigned(original_file["byte_size"])
                    or original_file["byte_size"] > ownership["content"]["byte_size"]
                ):
                    raise ValueError("Replica staging cleanup original file observation differs")
            item_id = content._digest([operation, ordinal])
            if item_id not in items:
                raise ValueError("Replica staging cleanup lost an original mandatory item")
            row = content._closed(
                items[item_id], {"operation_id", "ordinal", "intent_digest", "outcome"}
            )
            if (
                row["operation_id"] != operation
                or type(row["ordinal"]) is not int
                or row["ordinal"] != ordinal
                or row["intent_digest"] != content._digest(intent)
            ):
                raise ValueError("Replica staging cleanup item changed original command/position")
            outcome = row["outcome"]
            if outcome is not None:
                fields = {"state", "observed_ms"}
                if original_file is not None:
                    fields.add("observed_digest")
                outcome = content._closed(outcome, fields)
                if (
                    outcome["state"] != ("deleted" if original_file is not None else "missing")
                    or not content._completed(intent["budget"], outcome["observed_ms"])
                    or (
                        original_file is not None
                        and (
                            not isinstance(outcome["observed_digest"], str)
                            or content.SHA.fullmatch(outcome["observed_digest"]) is None
                        )
                    )
                ):
                    raise ValueError("Replica staging cleanup original outcome/budget differs")
            owned.add(item_id)
    if owned != set(items):
        raise ValueError("Replica staging cleanup item inventory has unrelated original rows")
