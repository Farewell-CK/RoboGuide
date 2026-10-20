"""Closed deployment and native configuration gates; positive process evidence is separate."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from observability_profile import endpoint, generate, validate


def profile() -> dict[str, Any]:
    """Declare only local service endpoints, never inventory, device facts or actual SLO success."""
    credentials = {"ca_file": None, "cert_file": None, "key_file": None, "server_name": None}
    return {
        "schema": "roboguide.observability-profile/v0.1",
        "profile_id": "unit-software",
        "revision": 1,
        "cluster": "configured-cluster",
        "domain": "configured-domain",
        "scrape_seconds": 2,
        "evaluation_seconds": 2,
        "pending_seconds": 10,
        "latency_seconds": 1,
        "backlog_messages": 8,
        "alertmanager": {"url": "http://127.0.0.1:19093", **credentials},
        "receiver": {"url": "http://127.0.0.1:19094/events", **credentials},
        "services": [
            {
                "service_id": "declared-controller",
                "kind": "controller",
                "url": "http://127.0.0.1:19090",
                **credentials,
            }
        ],
    }


def test_native_configs_preserve_declared_auth_labels_and_no_false_success(tmp_path: Path) -> None:
    """Render native files while retaining original timing and closed declared services."""
    source = profile()
    before = copy.deepcopy(source)
    output = tmp_path / "new-profile"
    receipt = generate(source, output)
    assert source == before and receipt["slo_achieved"] is False and receipt["authority"] is False
    prometheus = json.loads((output / "prometheus.json").read_bytes())
    assert prometheus["scrape_configs"][0]["static_configs"][0]["targets"] == ["127.0.0.1:19090"]
    assert prometheus["scrape_configs"][0]["honor_labels"] is False
    assert prometheus["scrape_configs"][0]["follow_redirects"] is False
    assert prometheus["global"]["scrape_interval"] == "2s"
    assert all(
        rule["for"] == "10s"
        for rule in json.loads((output / "rules.json").read_bytes())["groups"][0]["rules"]
    )
    assert output.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        generate(source, output)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:90",
        "http://10.0.0.1:90",
        "http://example.com:90",
        "https://user:password@example.com:443",
        "https://example.com",
        "https://example.com:443/path",
        "https://example.com:443?redirect=elsewhere",
        "https://example.com:443/#fragment",
    ],
)
def test_endpoint_cannot_ignore_credentials_or_expand_plaintext_scope(url: str) -> None:
    """Prevent plaintext DNS/nonlocal routes and hidden path, credentials or redirect identity."""
    with pytest.raises(ValueError):
        endpoint(url)


@pytest.mark.parametrize(
    "change", ["unknown", "empty", "repeat", "boolean", "window", "kind", "tls"]
)
def test_invalid_profile_refuses_before_output_mutation(tmp_path: Path, change: str) -> None:
    """Freeze complete deployment limits and refuse unknown/duplicate/ignored fields."""
    source = profile()
    if change == "unknown":
        source["automatic_devices"] = []
    elif change == "empty":
        source["services"] = []
    elif change == "repeat":
        source["services"] *= 2
    elif change == "boolean":
        source["revision"] = True
    elif change == "window":
        source["pending_seconds"] = 1
    elif change == "kind":
        source["services"][0]["kind"] = "invented-service"
    elif change == "tls":
        source["services"][0]["key_file"] = "/unused-key"
    with pytest.raises(ValueError):
        generate(source, tmp_path / "unpublished")
    assert not (tmp_path / "unpublished").exists()


def test_actual_invalid_tls_files_and_symlink_output_are_rejected(tmp_path: Path) -> None:
    """A path declaration cannot masquerade as parseable TLS material or bypass private output."""
    source = profile()
    invalid = tmp_path / "invalid-cert"
    invalid.write_bytes(b"not a TLS certificate")
    source["services"][0].update(
        url="https://127.0.0.1:19090",
        ca_file=str(invalid),
        cert_file=str(invalid),
        key_file=str(invalid),
        server_name="localhost",
    )
    with pytest.raises(ValueError):
        validate(source)
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError):
        generate(profile(), linked / "output")
    assert not (actual / "output").exists()
