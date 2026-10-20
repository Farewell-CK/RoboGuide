"""Observe real Prometheus scrapes and Alertmanager delivery to an owned endpoint."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from check_identity_processes import client
from check_identity_processes import request as tls_request
from check_state_verifier_processes import ROOT, address, save, wait_for


class Receiver(ThreadingHTTPServer):
    """Own only this invocation's private loopback alert destination and bounded actual messages."""

    def __init__(self) -> None:
        """Bind an ephemeral private endpoint; it cannot send email/chat or contact other users."""
        super().__init__(("127.0.0.1", 0), ReceiverHandler)
        self.records: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def snapshot(self) -> list[dict[str, Any]]:
        """Read actual native Alertmanager messages without manufacturing an alert."""
        with self.lock:
            return list(self.records)


class ReceiverHandler(BaseHTTPRequestHandler):
    """Accept bounded native webhook messages from the local conformance Alertmanager."""

    def do_POST(self) -> None:  # noqa: N802
        """Retain actual firing/resolved payloads; malformed or excess messages are rejected."""
        receiver: Receiver = self.server  # type: ignore[assignment]
        count = int(self.headers.get("Content-Length", "0"))
        if self.path != "/alerts" or not 0 < count <= 256 * 1024:
            self.send_error(400)
            return
        value = json.loads(self.rfile.read(count))
        if not isinstance(value, dict) or value.get("status") not in {"firing", "resolved"}:
            self.send_error(400)
            return
        with receiver.lock:
            if len(receiver.records) >= 128:
                self.send_error(429)
                return
            receiver.records.append(value)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Keep only structured original messages; access logs cannot become evidence."""


class MonitoringProcesses:
    """Own real monitoring binaries, frozen configuration and independent alert reception."""

    def __init__(
        self,
        directory: Path,
        tools: Path,
        origin: str,
        start: Callable[..., subprocess.Popen[bytes]],
    ) -> None:
        """Pin explicit tool bytes and routes; no software metrics are fabricated here."""
        self.directory, self.origin, self.spawn = directory, origin, start
        self.root = directory / "monitoring"
        self.root.mkdir(mode=0o700)
        self.prometheus = tools / "prometheus-3.13.4.linux-amd64/prometheus"
        self.promtool = self.prometheus.with_name("promtool")
        self.alertmanager = tools / "alertmanager-0.34.1.linux-amd64/alertmanager"
        self.amtool = self.alertmanager.with_name("amtool")
        self.identities = {
            str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (self.prometheus, self.promtool, self.alertmanager, self.amtool)
        }
        self.receiver: Receiver | None = None
        self.thread: threading.Thread | None = None
        self.observed: list[dict[str, Any]] = []
        self.endpoint = "http://" + address()
        self.alert_endpoint = "http://" + address()

    def api(self, path: str) -> dict[str, Any]:
        """Read the actual bounded local Prometheus query API without proxy or retry mutations."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(self.endpoint + path, timeout=3) as response:  # noqa: S310
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("actual Prometheus response exceeds its bound")
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("status") != "success":
            raise ValueError("actual Prometheus query failed")
        return value

    def start(self) -> None:
        """Render/validate native configs, then start real scrape and alert delivery processes."""
        self.receiver = Receiver()
        self.thread = threading.Thread(target=self.receiver.serve_forever, daemon=True)
        self.thread.start()
        empty = {"ca_file": None, "cert_file": None, "key_file": None, "server_name": None}
        profile = {
            "schema": "roboguide.observability-profile/v0.1",
            "profile_id": "actual-software-conformance",
            "revision": 1,
            "cluster": "owned-processes",
            "domain": "owned-loopback",
            "scrape_seconds": 1,
            "evaluation_seconds": 1,
            "pending_seconds": 1,
            "latency_seconds": 2,
            "backlog_messages": 32,
            "alertmanager": {"url": self.alert_endpoint, **empty},
            "receiver": {
                "url": f"http://127.0.0.1:{self.receiver.server_address[1]}/alerts",
                **empty,
            },
            "services": [
                {
                    "service_id": "actual-controller",
                    "kind": "controller",
                    "url": self.origin,
                    "ca_file": str(self.directory / "pki/ca.crt"),
                    "cert_file": str(self.directory / "pki/admin-next.crt"),
                    "key_file": str(self.directory / "pki/admin-next.key"),
                    "server_name": "localhost",
                }
            ],
        }
        profile_path = self.root / "profile.json"
        save(profile_path, profile)
        config = self.root / "native"
        rendered = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/observability/observability_profile.py"),
                "--profile",
                str(profile_path),
                "--output",
                str(config),
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
        save(self.root / "render-result.json", json.loads(rendered.stdout))
        checks = [
            (
                [str(self.promtool), "check", "config", str(config / "prometheus.json")],
                "promtool-config",
            ),
            ([str(self.promtool), "check", "rules", str(config / "rules.json")], "promtool-rules"),
            (
                [str(self.amtool), "check-config", str(config / "alertmanager.json")],
                "amtool-config",
            ),
        ]
        for arguments, name in checks:
            checked = subprocess.run(arguments, capture_output=True, timeout=15, check=False)
            (self.root / (name + ".log")).write_bytes(checked.stdout + checked.stderr)
            assert checked.returncode == 0, name
        self.spawn(
            [
                str(self.alertmanager),
                "--config.file=" + str(config / "alertmanager.json"),
                "--storage.path=" + str(self.root / "alert-storage"),
                "--web.listen-address=" + self.alert_endpoint.removeprefix("http://"),
                "--cluster.listen-address=",
            ],
            "actual-alertmanager",
        )
        self.spawn(
            [
                str(self.prometheus),
                "--config.file=" + str(config / "prometheus.json"),
                "--storage.tsdb.path=" + str(self.root / "timeseries"),
                "--storage.tsdb.retention.time=1h",
                "--storage.tsdb.retention.size=64MB",
                "--web.listen-address=" + self.endpoint.removeprefix("http://"),
                "--web.external-url=" + self.endpoint,
            ],
            "actual-prometheus",
        )
        query = urllib.parse.urlencode({"query": 'up{job="actual-controller"}'})
        scraped = wait_for(
            lambda: self.api("/api/v1/query?" + query)["data"]["result"],
            "actual mTLS Prometheus scrape",
        )
        assert len(scraped) == 1 and scraped[0]["value"][1] == "1", scraped
        # Actual global metrics-only delegation grants no business reads or mutations.
        assert (
            tls_request(
                self.origin,
                client(self.directory / "pki", "admin-next"),
                "/v1/authority/recover",
                {},
            )[0]
            == 403
        )
        save(self.root / "initial-scrape.json", scraped)
        save(self.root / "tool-identities.json", self.identities)

    def observe(self, state: str, kind: str) -> None:
        """Require native alert evaluation and an independently received matching notification."""
        assert self.receiver is not None
        expected = "RoboGuideAuthorityWriteDisabled"

        def received() -> list[dict[str, Any]] | None:
            """Read only this original fault's real Alertmanager messages at the owned receiver."""
            assert self.receiver is not None
            rows = [
                message
                for message in self.receiver.snapshot()
                if message.get("status") == state
                and any(
                    alert.get("labels", {}).get("alertname") == expected
                    for alert in message.get("alerts", [])
                )
            ]
            # Each of the two original SQL windows must produce its own firing/resolved pair.
            return (
                rows
                if len(rows) > sum(record["state"] == state for record in self.observed)
                else None
            )

        rows = wait_for(received, "actual Alertmanager " + state, seconds=25)
        query = urllib.parse.urlencode(
            {"query": 'roboguide_authority_write_enabled{job="actual-controller"}'}
        )
        actual = self.api("/api/v1/query?" + query)
        values = actual["data"]["result"]
        assert len(values) == 1 and values[0]["value"][1] == ("0" if state == "firing" else "1"), (
            actual
        )
        if state == "firing":
            active = self.api("/api/v1/alerts")["data"]["alerts"]
            assert any(
                alert["labels"]["alertname"] == expected and alert["state"] == "firing"
                for alert in active
            ), active
        record = {
            "state": state,
            "original_fault": kind,
            "messages": rows,
            "actual_prometheus_query": actual,
        }
        save(self.root / f"{kind}-{state}.json", record)
        self.observed.append(record)

    def result(self) -> dict[str, Any]:
        """Report measured actual delivery; this short software window is not a 30-day SLO."""
        assert [record["state"] for record in self.observed] == [
            "firing",
            "resolved",
            "firing",
            "resolved",
        ]
        return {
            "passed": True,
            "actual_mtls_scraper": True,
            "actual_alertmanager": True,
            "independent_loopback_receiver": True,
            "firing_deliveries": 2,
            "resolved_deliveries": 2,
            "scraper_has_mutation_authority": False,
            "slo_achieved": False,
            "tool_identities": self.identities,
        }

    def close(self) -> None:
        """Close only the owned receiver; the caller reaps all actual spawned monitor processes."""
        if self.receiver is not None:
            save(self.root / "received-alerts.json", self.receiver.snapshot())
            self.receiver.shutdown()
            self.receiver.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)
