"""Verify original Root6 prerequisite journals without granting receiving or Snapshot authority."""

from __future__ import annotations

from typing import Any

from . import replica_content as content
from .snapshot_references import OperationBudget


def _membership(value: Any) -> tuple[set[int], dict[str, Any]]:
    """Check complete bounded original member/Root declarations, independently of live consensus."""
    stored = content._closed(value, {"log_id", "membership"})
    membership = content._closed(stored["membership"], {"configs", "nodes"})
    groups, nodes = membership["configs"], membership["nodes"]
    if (
        stored["log_id"] is None
        or not isinstance(groups, list)
        or not 1 <= len(groups) <= 2
        or not isinstance(nodes, dict)
        or not 1 <= len(nodes) <= 9
        or any(
            not isinstance(group, list)
            or not group
            or any(type(member) is not int or member < 0 for member in group)
            or group != sorted(set(group))
            for group in groups
        )
    ):
        raise ValueError("Snapshot receive original membership is incomplete or unbounded")
    voters = {member for group in groups for member in group}
    for member, node in nodes.items():
        content._closed(node, {"addr", "content_store_owner"})
        if (
            not isinstance(member, str)
            or not member.isdecimal()
            or str(int(member)) != member
            or not isinstance(node["addr"], str)
            or not node["addr"]
            or not isinstance(node["content_store_owner"], str)
            or not node["content_store_owner"].startswith("replica-content:")
            or content.HEX.fullmatch(node["content_store_owner"][16:]) is None
        ):
            raise ValueError("Snapshot receive original member/Root differs")
    if any(str(member) not in nodes for member in voters):
        raise ValueError("Snapshot receive omitted an original voter Root")
    return voters, nodes


def validate(identity: dict[str, Any], tables: dict[str, Any], budget: OperationBudget) -> set[str]:
    """Bind original receives and receipts to exact pins, preserving incomplete state."""
    rows = tables.get("content_snapshot_receives", {})
    if len(rows) > 4096:
        raise ValueError("Snapshot receive history exceeds its original bound")
    owned: set[str] = set()
    for key, row in rows.items():
        budget.check()
        content._closed(row, {"intent", "ready", "retirement"})
        intent = content._closed(row["intent"], {"command_id", "receiver", "header", "budget"})
        header = content._closed(
            intent["header"],
            {
                "schema",
                "source_member",
                "source_store_owner",
                "source_leaf",
                "meta",
                "payload_digest",
                "target",
            },
        )
        target = content._closed(
            header["target"],
            {
                "profile_identity",
                "epoch",
                "revision",
                "checkpoint_digest",
                "batch_digest",
                "membership",
                "member_stores",
                "inputs",
            },
        )
        meta = content._closed(header["meta"], {"last_log_id", "last_membership", "snapshot_id"})
        content._budget(intent["budget"])
        epoch = content._closed(target["epoch"], {"term", "replica_id"})
        voters, nodes = _membership(target["membership"])
        _membership(meta["last_membership"])
        log = content._closed(meta["last_log_id"], {"leader_id", "index"})
        content._closed(log["leader_id"], {"term", "node_id"})
        if (
            intent["receiver"] != identity
            or header["schema"] != "roboguide.snapshot-content-header/v0.1"
            or type(header["source_member"]) is not int
            or header["source_member"] == identity["member"]
            or header["source_member"] != epoch["replica_id"]
            or header["source_member"] not in voters
            or type(epoch["term"]) is not int
            or epoch["term"] <= 0
            or type(target["revision"]) is not int
            or target["revision"] <= 0
            or target["profile_identity"] != identity["profile_identity"]
            or target["member_stores"]
            != {str(member): nodes[str(member)]["content_store_owner"] for member in voters}
            or len(set(target["member_stores"].values())) != len(voters)
            or nodes[str(header["source_member"])]["content_store_owner"]
            != header["source_store_owner"]
            or nodes.get(str(identity["member"]), {}).get("content_store_owner")
            != identity["store_owner"]
            or not isinstance(header["source_leaf"], str)
            or content.SHA.fullmatch(header["source_leaf"]) is None
            or any(
                not isinstance(value, str) or content.HEX.fullmatch(value) is None
                for value in (
                    header["payload_digest"],
                    target["batch_digest"],
                    target["checkpoint_digest"],
                )
            )
            or meta["snapshot_id"]
            != f"{log['index']}-{target['revision']}-{header['payload_digest']}"
            or not isinstance(target["inputs"], list)
            or len(target["inputs"]) > 16384
        ):
            raise ValueError("Snapshot receive original source/receiver/header differs")
        inputs = [content._content(value) for value in target["inputs"]]
        if inputs != sorted(set(inputs)) or len({value[:2] for value in inputs}) != len(inputs):
            raise ValueError("Snapshot receive original input identities conflict")
        command = "snapshot-receive:" + content._digest(
            [
                target["profile_identity"],
                header["source_member"],
                identity["member"],
                meta["snapshot_id"],
                header["payload_digest"],
            ]
        )
        if intent["command_id"] != command or content._digest(command) != key:
            raise ValueError("Snapshot receive original command changed its identity")
        ready, retirement = row["ready"], row["retirement"]
        if retirement is not None and (
            ready is None
            or not isinstance(retirement, str)
            or content.HEX.fullmatch(retirement) is None
        ):
            raise ValueError("Snapshot receive retirement lacks original readiness")
        if ready is not None:
            content._closed(ready, {"intent_digest", "inputs", "verified_files", "completed_ms"})
            if (
                ready["intent_digest"] != content._digest(intent)
                or not content._completed(intent["budget"], ready["completed_ms"])
                or not isinstance(ready["inputs"], list)
                or not isinstance(ready["verified_files"], list)
                or len(ready["verified_files"]) != len(ready["inputs"])
                or any(
                    not isinstance(value, list)
                    or len(value) != 2
                    or any(type(part) is not int or not 0 <= part < 2**64 for part in value)
                    or value[1] == 0
                    for value in ready["verified_files"]
                )
                or [value["content"] for value in ready["inputs"]] != target["inputs"]
            ):
                raise ValueError(
                    "Snapshot receive byte readiness changed its original issue/budget"
                )
            for receipt in ready["inputs"]:
                content._closed(
                    receipt,
                    {
                        "schema",
                        "request_digest",
                        "command_id",
                        "content",
                        "store_owner",
                        "member",
                        "profile_identity",
                        "file_device",
                        "file_inode",
                        "completed_ms",
                    },
                )
                table = (
                    "content_streams"
                    if receipt["schema"] == "roboguide.replica-content-stream-receipt/v0.1"
                    else "content_operations"
                )
                source = tables[table].get(content._digest(receipt["command_id"]))
                if (
                    receipt["store_owner"] != identity["store_owner"]
                    or receipt["member"] != identity["member"]
                    or receipt["profile_identity"] != identity["profile_identity"]
                    or receipt["file_inode"] <= 0
                    or source is None
                    or source["receipt"] != receipt
                    or (
                        retirement is None
                        and tables["content_objects"].get(content._digest(receipt["content"]))
                        != receipt
                    )
                ):
                    raise ValueError(
                        "Snapshot receive byte readiness lost its original object receipt"
                    )
        for value in target["inputs"]:
            pin = "snapshot-receive:" + key + ":" + content._digest(value)
            content._pin(
                tables,
                pin,
                "snapshot-receive",
                value,
                content._digest(intent),
                active=retirement is None,
                release=retirement,
            )
            owned.add(pin)
    return owned
