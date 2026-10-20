"""Render closed deployment inputs into native Prometheus and Alertmanager configuration."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import ssl
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z")
METRICS = {
    "controller": "/metrics",
    "mission": "/metrics/prometheus",
    "artifact": "/metrics/prometheus",
    "replica": "/metrics",
}
TLS_FIELDS = {"ca_file", "cert_file", "key_file", "server_name"}


def bounded_identifier(value: object) -> str:
    """Require a fixed deployment identifier without expression or label interpolation syntax."""
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise ValueError("invalid bounded observability identity")
    return value


def endpoint(value: object, *, path: bool = False) -> tuple[str, str]:
    """Allow authenticated HTTPS or explicit numeric loopback HTTP without ambient routing."""
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(character) < 33 for character in value)
    ):
        raise ValueError("invalid observability endpoint")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("observability endpoint must be an explicit HTTP(S) origin")
    if parsed.port is None or parsed.port == 0 or (not path and parsed.path not in {"", "/"}):
        raise ValueError("observability endpoint requires a nonzero port and declared path")
    if parsed.scheme == "http":
        try:
            private = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            private = False
        if not private:
            raise ValueError("plaintext metrics/alerts require numeric loopback")
    return parsed.scheme, parsed.netloc


def tls_config(item: dict[str, Any], scheme: str) -> dict[str, Any] | None:
    """Verify actual configured TLS files; plaintext profiles cannot hide ignored credentials."""
    if scheme == "http":
        if any(item[key] is not None for key in TLS_FIELDS):
            raise ValueError("loopback plaintext cannot declare unused TLS identity")
        return None
    paths = {}
    for key in ("ca_file", "cert_file", "key_file"):
        value = item[key]
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or len(value) > 4096
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("TLS credentials require actual absolute regular-file paths")
        location = Path(value)
        for parent in (*reversed(location.parents), location):
            metadata = parent.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("TLS credential paths cannot traverse symlinks")
        if not location.is_file() or location.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("TLS credential file is unavailable or exceeds its bound")
        paths[key] = str(location)
    server_name = item["server_name"]
    if (
        not isinstance(server_name, str)
        or not server_name
        or len(server_name) > 253
        or not re.fullmatch(r"[a-zA-Z0-9.:-]+", server_name)
    ):
        raise ValueError("actual TLS server name is required")
    try:
        context = ssl.create_default_context(cafile=paths["ca_file"])
        context.load_cert_chain(paths["cert_file"], paths["key_file"])
    except (OSError, ValueError) as error:
        raise ValueError("actual observability TLS credentials cannot be parsed") from error
    return {**paths, "server_name": server_name, "min_version": "TLS12"}


def validate(profile: dict[str, Any]) -> dict[str, Any]:
    """Validate all endpoints, limits and actual TLS material before creating any output."""
    if (
        set(profile)
        != {
            "schema",
            "profile_id",
            "revision",
            "cluster",
            "domain",
            "scrape_seconds",
            "evaluation_seconds",
            "pending_seconds",
            "latency_seconds",
            "backlog_messages",
            "alertmanager",
            "receiver",
            "services",
        }
        or profile["schema"] != "roboguide.observability-profile/v0.1"
    ):
        raise ValueError("observability profile is not closed or versioned")
    for name in ("profile_id", "cluster", "domain"):
        bounded_identifier(profile[name])
    for name, maximum in (
        ("revision", 2**63 - 1),
        ("scrape_seconds", 300),
        ("evaluation_seconds", 300),
        ("pending_seconds", 3600),
        ("latency_seconds", 300),
        ("backlog_messages", 1_000_000),
    ):
        if type(profile[name]) is not int or not 1 <= profile[name] <= maximum:
            raise ValueError("invalid original observability bound: " + name)
    if (
        profile["scrape_seconds"] > profile["pending_seconds"]
        or profile["evaluation_seconds"] > profile["pending_seconds"]
    ):
        raise ValueError("alert pending window cannot be shorter than a scrape/evaluation interval")
    alertmanager = profile["alertmanager"]
    if not isinstance(alertmanager, dict) or set(alertmanager) != {"url", *TLS_FIELDS}:
        raise ValueError("Alertmanager endpoint is not closed")
    scheme, target = endpoint(alertmanager["url"])
    alert_target: dict[str, Any] = {"scheme": scheme, "static_configs": [{"targets": [target]}]}
    if tls := tls_config(alertmanager, scheme):
        alert_target["tls_config"] = tls
    receiver = profile["receiver"]
    if not isinstance(receiver, dict) or set(receiver) != {"url", *TLS_FIELDS}:
        raise ValueError("explicit alert receiver is not closed")
    receiver_scheme, _ = endpoint(receiver["url"], path=True)
    webhook: dict[str, Any] = {"url": receiver["url"], "send_resolved": True, "max_alerts": 64}
    if tls := tls_config(receiver, receiver_scheme):
        webhook["http_config"] = {"tls_config": tls, "follow_redirects": False}
    services = profile["services"]
    if not isinstance(services, list) or not 1 <= len(services) <= 64:
        raise ValueError("observability services must be explicitly bounded")
    jobs = []
    identifiers: set[str] = set()
    targets: set[str] = set()
    for service in services:
        if not isinstance(service, dict) or set(service) != {
            "service_id",
            "kind",
            "url",
            *TLS_FIELDS,
        }:
            raise ValueError("observability service is not closed")
        identifier = bounded_identifier(service["service_id"])
        if identifier in identifiers or service["url"] in targets or service["kind"] not in METRICS:
            raise ValueError("duplicate service/target or unsupported metric contract")
        identifiers.add(identifier)
        targets.add(service["url"])
        scheme, target = endpoint(service["url"])
        job: dict[str, Any] = {
            "job_name": identifier,
            "scheme": scheme,
            "metrics_path": METRICS[service["kind"]],
            "honor_labels": False,
            "follow_redirects": False,
            "scrape_timeout": f"{profile['scrape_seconds']}s",
            "sample_limit": 10000,
            "label_limit": 16,
            "label_value_length_limit": 256,
            "body_size_limit": "2MB",
            "static_configs": [
                {
                    "targets": [target],
                    "labels": {
                        "service": service["kind"],
                        "cluster": profile["cluster"],
                        "domain": profile["domain"],
                    },
                }
            ],
        }
        if tls := tls_config(service, scheme):
            job["tls_config"] = tls
        jobs.append(job)
    return {"alertmanager": alert_target, "webhook": webhook, "jobs": jobs}


def rules(profile: dict[str, Any]) -> dict[str, Any]:
    """Describe fixed rule semantics using only frozen numeric deployment thresholds."""
    definitions = [
        (
            "RoboGuideScrapeUnavailable",
            "up == 0",
            "critical",
            "The declared service cannot be scraped; absence is not healthy.",
        ),
        (
            "RoboGuideMetricsContractMissing",
            '(up{service="controller"} unless on(job,instance) roboguide_authority_write_enabled) '
            'or (up{service="mission"} unless on(job,instance) roboguide_mission_requests_total) '
            'or (up{service="artifact"} unless on(job,instance) roboguide_artifact_map_revisions) '
            'or (up{service="replica"} unless on(job,instance) '
            "roboguide_replica_accepted_revision)",
            "critical",
            "The declared scrape lacks its service contract; a 200 response is not coverage.",
        ),
        (
            "RoboGuideAuthorityWriteDisabled",
            "roboguide_authority_write_enabled == 0",
            "critical",
            "Controller retains committed queries and rejects mutations; "
            "inspect the original write-recovery status.",
        ),
        (
            "RoboGuideRuntimeUnknown",
            "roboguide_runtime_unknown_attempts > 0",
            "critical",
            "Unknown attempts retain ownership; reconcile original evidence before release/repeat.",
        ),
        (
            "RoboGuideStopManual",
            "roboguide_stop_purposes_manual > 0 or roboguide_stop_purposes_overdue > 0",
            "critical",
            "Original stop evidence is incomplete or overdue; Cancel acceptance is not stop proof.",
        ),
        (
            "RoboGuideMemoryUnresolved",
            "roboguide_memory_revoke_providers_unresolved > 0 "
            "or roboguide_memory_use_observations_exhausted > 0 "
            "or roboguide_memory_handoffs_manual > 0",
            "warning",
            "Reconcile original provider/session/budget; never replace import identity on retry.",
        ),
        (
            "RoboGuideObservationIncomplete",
            "roboguide_observability_available == 0 "
            "or increase(roboguide_observability_dropped_total[5m]) > 0 "
            "or roboguide_protocol_queue_complete == 0",
            "warning",
            "Observation/queue coverage is incomplete; latency/queue SLO satisfaction is unknown.",
        ),
        (
            "RoboGuideQueueBacklog",
            f"roboguide_protocol_queue_depth > {profile['backlog_messages']}",
            "warning",
            "Actual tracked protocol work exceeds this deployment's frozen backlog limit.",
        ),
        (
            "RoboGuideControllerLatency",
            "histogram_quantile(0.99, sum by (le,instance,job,cluster,domain,stage) "
            "(rate(roboguide_controller_stage_duration_seconds_bucket"
            f'{{stage=~"http_request|ack|completion"}}[5m]))) > {profile["latency_seconds"]}',
            "warning",
            "Observed stage p99 exceeds its limit; inspect observation coverage and backlog.",
        ),
        (
            "RoboGuideReplicaQuorumUnavailable",
            "sum by(cluster,domain) (roboguide_replica_quorum_epoch_present) == 0",
            "critical",
            "No declared replica currently reports a majority epoch; do not force takeover.",
        ),
        (
            "RoboGuideReplicaRevisionLag",
            "max by(cluster,domain) (roboguide_replica_accepted_revision) "
            "- on(cluster,domain) group_right roboguide_replica_accepted_revision > 64",
            "warning",
            "An actual replica reports lag; verify accepted history and content before upgrade.",
        ),
    ]
    return {
        "groups": [
            {
                "name": "roboguide-software",
                "rules": [
                    {
                        "alert": name,
                        "expr": expression,
                        "for": f"{profile['pending_seconds']}s",
                        "labels": {"severity": severity},
                        "annotations": {
                            "summary": summary,
                            "runbook_url": "https://github.com/Farewell-CK/RoboGuide/blob/main/docs/development/observability-v0.1.md",
                        },
                    }
                    for name, expression, severity, summary in definitions
                ],
            }
        ]
    }


def generate(profile: dict[str, Any], output: Path) -> dict[str, Any]:
    """Render a new private deployment directory; existing outputs are never replaced."""
    selected = validate(profile)
    output = output.absolute()
    if any(parent.is_symlink() for parent in (output, *output.parents)):
        raise ValueError("observability output cannot traverse symlinks")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    prometheus = {
        "global": {
            "scrape_interval": f"{profile['scrape_seconds']}s",
            "evaluation_interval": f"{profile['evaluation_seconds']}s",
            "external_labels": {"cluster": profile["cluster"], "domain": profile["domain"]},
        },
        "alerting": {"alertmanagers": [selected["alertmanager"]]},
        "rule_files": [str(output / "rules.json")],
        "scrape_configs": selected["jobs"],
    }
    alertmanager = {
        "route": {
            "receiver": "explicit-deployment-receiver",
            "group_by": ["alertname", "cluster", "domain", "job", "instance"],
            "group_wait": f"{profile['evaluation_seconds']}s",
            "group_interval": f"{profile['evaluation_seconds']}s",
            "repeat_interval": "1h",
        },
        "receivers": [
            {"name": "explicit-deployment-receiver", "webhook_configs": [selected["webhook"]]}
        ],
    }
    documents = {
        "prometheus.json": prometheus,
        "alertmanager.json": alertmanager,
        "rules.json": rules(profile),
        "source-profile.json": profile,
    }
    digests = {}
    for name, value in documents.items():
        raw = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
        path = output / name
        with path.open("xb") as stream:
            path.chmod(0o600)
            stream.write(raw)
        digests[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
    receipt = {
        "schema": "roboguide.observability-render/v0.1",
        "profile_id": profile["profile_id"],
        "revision": profile["revision"],
        "digests": digests,
        "authority": False,
        "slo_achieved": False,
    }
    (output / "render-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    )
    return receipt


def main() -> None:
    """Render only explicitly supplied configuration; never start services or send notifications."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with arguments.profile.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("observability profile exceeds its byte bound")
    profile = json.loads(raw)
    if not isinstance(profile, dict):
        raise ValueError("observability profile must be an object")
    print(json.dumps(generate(profile, arguments.output), sort_keys=True))


if __name__ == "__main__":
    main()
