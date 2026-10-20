"""Compose an explicitly configured runtime MI worker without creating Missions or devices."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import tomllib
from pathlib import Path
from typing import Any

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.config import current_environment, load_settings
from mission.provenance import implementation_identity
from mission.responses import ResponsesMissionRepairer, ResponsesMissionReviewer
from mission.runtime_replanning import RuntimeReplanningWorker
from mission.runtime_replanning_http import JournaledRuntimeTransport, RuntimeJsonPeer
from mission.runtime_work_store import RuntimeWorkError, RuntimeWorkStore
from mission.submission_evidence import canonical_plan_digest


def _digest(path: Path) -> str:
    """Hash required original bytes and refuse unavailable evidence."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _peer(document: object, root: Path) -> RuntimeJsonPeer:
    """Build only the exact closed mTLS profile supplied by deployment."""
    if (
        not isinstance(document, dict)
        or set(document) != {"endpoint", "tenant", "principal", "certificate", "key", "ca"}
        or any(not isinstance(value, str) or not value.strip() for value in document.values())
    ):
        raise RuntimeWorkError("invalid closed runtime MI peer profile")
    return RuntimeJsonPeer(
        document["endpoint"],
        document["tenant"],
        document["principal"],
        root / document["certificate"],
        root / document["key"],
        root / document["ca"],
    )


def compose(
    path: Path, root: Path
) -> tuple[RuntimeWorkStore, RuntimeReplanningWorker, list[str], int]:
    """Freeze actual settings/assets and explicit peers before polling any original Request."""
    raw = path.read_bytes()
    document = tomllib.loads(raw.decode())
    if set(document) - {
        "schema_version",
        "state_db",
        "mission_config",
        "poll_ms",
        "request_ids",
        "service",
        "proposer",
        "reviewer",
        "discovery",
    } or document.get("schema_version") not in {
        "roboguide.runtime-mi-worker/v0.1",
        "roboguide.runtime-mi-worker/v0.2",
    }:
        raise RuntimeWorkError("unsupported runtime MI worker profile")
    requests = document.get("request_ids", [])
    poll = document.get("poll_ms", 1000)
    if (
        not isinstance(requests, list)
        or len(requests) > 128
        or any(
            not isinstance(identity, str) or not 1 <= len(identity) <= 128 for identity in requests
        )
        or len(set(requests)) != len(requests)
        or type(poll) is not int
        or not 250 <= poll <= 60000
    ):
        raise RuntimeWorkError("runtime MI request/poll bounds invalid")
    discovery = None
    if document["schema_version"] == "roboguide.runtime-mi-worker/v0.2":
        configured = document.get("discovery")
        if (
            requests
            or not isinstance(configured, dict)
            or set(configured) != {"page_size", "max_pages"}
            or type(configured["page_size"]) is not int
            or type(configured["max_pages"]) is not int
            or not 1 <= configured["page_size"] <= 32
            or not 1 <= configured["max_pages"] <= 4
        ):
            raise RuntimeWorkError("runtime MI discovery requires explicit bounded v0.2 input")
        discovery = (configured["page_size"], configured["max_pages"])
    elif "discovery" in document:
        raise RuntimeWorkError("legacy runtime MI profile grants no discovery")
    for field in ("state_db", "mission_config"):
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise RuntimeWorkError("runtime MI storage/configuration path absent")
    settings_path = root / document["mission_config"]
    settings = load_settings(settings_path, repository_root=root)
    if not settings.review_enabled:
        raise RuntimeWorkError("runtime MI requires explicitly configured semantic Review")
    catalog = CanonicalCapabilityCatalog.load(settings.capability_catalog_path)
    service, proposer = _peer(document.get("service"), root), _peer(document.get("proposer"), root)
    reviewer = _peer(document["reviewer"], root) if "reviewer" in document else None
    implementation = implementation_identity()
    identity = {
        "code_digest": implementation["source_digest"],
        "config_digest": canonical_plan_digest(
            {"worker": _digest(path), "mission": _digest(settings_path)}
        ),
        "catalog_digest": canonical_plan_digest(catalog.to_json()),
        "prompt_digest": canonical_plan_digest(
            {
                str(asset.relative_to(root)): _digest(asset)
                for asset in (
                    settings.schema_path,
                    settings.prompts.repairer_path,
                    settings.prompts.reviewer_path,
                )
            }
        ),
        "dependency_digest": _digest(root / "uv.lock"),
        "implementation": implementation,
        "peer_certificates": {
            name: {"leaf": _digest(root / value["certificate"]), "ca": _digest(root / value["ca"])}
            for name, value in document.items()
            if name in {"service", "proposer", "reviewer"}
        },
    }
    store = RuntimeWorkStore(root / document["state_db"])
    try:
        transport = JournaledRuntimeTransport(store)
        environment = current_environment()
        generator = ResponsesMissionRepairer(settings, environment, transport)
        semantic_reviewer = ResponsesMissionReviewer(settings, environment, transport)
        worker = RuntimeReplanningWorker(
            store,
            service,
            proposer,
            reviewer,
            transport,
            generator,
            semantic_reviewer,
            catalog,
            identity,
            discovery,
        )
    except BaseException:
        store.close()
        raise
    return store, worker, requests, poll


def main() -> int:
    """Run bounded original-ticket polling with explicit cleanup and no implicit authorization."""
    parser = argparse.ArgumentParser(prog="mission-runtime-replanner")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--observe-work")
    arguments = parser.parse_args()
    store, worker, requests, poll = compose(arguments.config, arguments.repository_root.resolve())
    try:
        if arguments.observe_work is not None:
            print(json.dumps(worker.observe(arguments.observe_work), sort_keys=True), flush=True)
            return 0
        while True:
            try:
                current = worker.poll_requests(requests)
            except (RuntimeWorkError, OSError, ValueError) as error:
                print(
                    json.dumps({"status": "discovery_held", "reason": str(error)[:512]}), flush=True
                )
                if arguments.once:
                    return 1
                current = []
            for request_id in current:
                result: dict[str, Any]
                try:
                    result = worker.run_once(request_id)
                except (RuntimeWorkError, OSError, ValueError) as error:
                    result = {
                        "request_id": request_id,
                        "status": "held",
                        "error_type": type(error).__name__,
                        "reason": str(error)[:512],
                    }
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            if arguments.once:
                return 0
            time.sleep(poll / 1000)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
