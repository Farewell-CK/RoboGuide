"""Check active Federation references across actual original complete domain authorities."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .federation_recovery import _digest
from .snapshot_references import OperationBudget


def _terminal(record: dict[str, Any]) -> bool:
    """Treat only complete original goal or complete stop coverage as released root history."""
    participants = set(record["manifest"]["participants"])
    return participants <= set(record["completed"]) or participants <= set(record["stopped"])


def _equal(left: Any, right: Any) -> bool:
    """Compare retained identities with the existing exact JSON-type/float-bit contract."""
    return _digest(left) == _digest(right)


def validate_domains(
    states: Sequence[tuple[str, list[dict[str, Any]], dict[str, Any] | None]],
    budget: OperationBudget,
) -> None:
    """Require every active original domain/proof without granting restored dispatch authority."""
    domains: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for profile, _rows, state in states:
        budget.check()
        if state is None:
            continue
        wrapper = json.loads(state["candidate"]["batch"]["checkpoint"]["checkpoint_json"])
        federation = wrapper.get("federation", {})
        if not federation:
            continue
        names = {policy["local_domain"] for policy in federation.get("policies", {}).values()} | {
            original["local_domain"]
            for original in federation.get("coordinations", {}).get("profiles", {}).values()
        }
        if not names:
            continue
        if len(names) != 1:
            raise ValueError("original Replica authority claims conflicting Control domains")
        domain = next(iter(names))
        previous, originals = domains.setdefault(domain, (profile, []))
        if previous != profile:
            raise ValueError("independent Replica profiles claim the same original Control domain")
        originals.append(federation)
    for local, (_profile, originals) in domains.items():
        for federation in originals:
            budget.check()
            roots = federation.get("coordinations", {}).get("transactions", {})
            for identity, record in roots.items():
                budget.check()
                if _terminal(record):
                    continue
                manifest = record["manifest"]
                if (
                    manifest["coordinator_domain"] != local
                    or not set(manifest["participants"]) <= domains.keys()
                ):
                    raise ValueError("active Federation root lacks its complete original domains")
                for domain, receipt in record["prepared"].items():
                    budget.check()
                    peers = domains[domain][1]
                    matching = []
                    for peer in peers:
                        probe = peer["executions"]["probes"].get(identity)
                        prepared = peer["executions"]["prepared"].get(identity)
                        if (
                            probe is not None
                            and prepared is not None
                            and (
                                probe["nonce"] == record["nonces"][domain]
                                and _equal(probe["manifest"], manifest)
                                and _equal(prepared["receipt"], receipt)
                            )
                        ):
                            matching.append(prepared)
                    if not matching:
                        raise ValueError(
                            "active Federation preparation lacks its original peer store"
                        )
                    if domain in record["commits"] and not any(
                        _equal(peer["committed"], record["commits"][domain])
                        and _equal(peer["decision"], record["decision"])
                        for peer in matching
                    ):
                        raise ValueError("active Federation Commit lacks its actual original peer")
                    if domain in record["released"] and not any(
                        _equal(peer["release"], record["commits"]) for peer in matching
                    ):
                        raise ValueError(
                            "active Federation release lacks its original complete proofs"
                        )
            for identity, prepared in federation.get("executions", {}).get("prepared", {}).items():
                budget.check()
                if prepared["stopped"] is not None or any(
                    evidence.get("completed_time") is not None
                    for evidence in prepared["goal_evidence"].values()
                ):
                    continue
                probe = federation["executions"]["probes"][identity]
                manifest = probe["manifest"]
                root_domain = manifest["coordinator_domain"]
                if root_domain not in domains:
                    raise ValueError("active Federation participant lacks its original root domain")
                roots = [
                    peer["coordinations"]["transactions"].get(identity)
                    for peer in domains[root_domain][1]
                ]
                if not any(
                    root is not None
                    and _equal(root["manifest"], manifest)
                    and root["nonces"].get(local) == probe["nonce"]
                    for root in roots
                ):
                    raise ValueError("active Federation participant lost its original root intent")
