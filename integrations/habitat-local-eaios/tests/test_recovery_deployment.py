"""Run-local recovery registration and live declaration consistency, without Habitat."""

from __future__ import annotations

import copy
import json
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from habitat_local_eaios.execution_recovery import execution_recovery_profile  # noqa: E402
from habitat_local_eaios.model import IntegrationError  # noqa: E402
from habitat_local_eaios.recovery_deployment import (  # noqa: E402
    prepare_deployment,
    render_node_config,
    verify_deployment,
)


def _source(node: str = "a") -> str:
    """Read maintained deployment input rather than creating a different capability contract."""
    return (ROOT.parents[1] / f"scenarios/e1-shared-world-episode-51/node-{node}.toml").read_text()


def _copies(directory: Path) -> list[Path]:
    """Copy both templates so tests can never change production deployment defaults."""
    paths = [directory / f"node-{node}.toml" for node in ("a", "b")]
    for path, node in zip(paths, ("a", "b"), strict=True):
        path.write_text(_source(node))
    return paths


def test_default_off_preserves_source_bytes_and_facts(tmp_path: Path) -> None:
    """Default runs preserve all original declarations and freeze exact copied bytes."""
    paths = _copies(tmp_path)
    before = [path.read_bytes() for path in paths]
    snapshot = tmp_path / "deployment.json"
    prepare_deployment(paths, snapshot, False)
    assert [path.read_bytes() for path in paths] == before
    frozen = json.loads(snapshot.read_text())
    assert frozen["retain_stopped_session"] is False
    assert all(
        node["profile"] == execution_recovery_profile(shared_world=True) for node in frozen["nodes"]
    )


def test_opt_in_changes_only_exact_operation_owner_metadata(tmp_path: Path) -> None:
    """Resources, floor facts, workflows and endpoints retain their complete original values."""
    paths = _copies(tmp_path)
    expected = [tomllib.loads(path.read_text()) for path in paths]
    profile = execution_recovery_profile(shared_world=True, retain_stopped_session=True)
    prepare_deployment(paths, tmp_path / "deployment.json", True)
    for path, before in zip(paths, expected, strict=True):
        after = tomllib.loads(path.read_text())
        owner = next(x for x in after["local_systems"] if x["id"] == "habitat-local-eaios")
        assert json.loads(owner["metadata"]["roboguide.execution-recovery"]) == profile
        before_owner = next(x for x in before["local_systems"] if x["id"] == owner["id"])
        before_owner["metadata"]["roboguide.execution-recovery"] = owner["metadata"][
            "roboguide.execution-recovery"
        ]
        assert after == before
    assert "repeat-after-stop" not in _source()


@pytest.mark.parametrize(
    "old,new",
    [
        ("execution-group", "execution"),
        ("unsupported", "invented-mode"),
        ('"name":"move"', '"name":"invented-operation"'),
        ('operation = "mobility.move@v1"', 'operation = "mobility.other@v1"'),
        ('owner = "habitat-local-eaios"', 'owner = "unknown-owner"'),
        ('schema = "roboguide.node-config/v0.7"', 'schema = "roboguide.node-config/v0.6"'),
    ],
)
def test_inconsistent_source_refuses_entire_set_before_any_write(
    tmp_path: Path, old: str, new: str
) -> None:
    """An invalid second config cannot cause a half-enabled deployment or invented support."""
    paths = _copies(tmp_path)
    paths[1].write_text(paths[1].read_text().replace(old, new))
    before = [path.read_bytes() for path in paths]
    with pytest.raises(IntegrationError):
        prepare_deployment(paths, tmp_path / "deployment.json", True)
    assert [path.read_bytes() for path in paths] == before
    assert not (tmp_path / "deployment.json").exists()


def test_missing_metadata_is_not_silently_invented() -> None:
    """A missing declaration exposes a deployment gap rather than guessed repeat permission."""
    text = "\n".join(
        line for line in _source().splitlines() if '"roboguide.execution-recovery" =' not in line
    )
    with pytest.raises(IntegrationError, match="missing or invalid"):
        render_node_config(text, True)


def test_duplicate_nodes_and_snapshot_reuse_fail_before_mutation(tmp_path: Path) -> None:
    """Distinctness here concerns registration identity, not Mission Actor semantics."""
    paths = _copies(tmp_path)
    before = [path.read_bytes() for path in paths]
    snapshot = tmp_path / "deployment.json"
    with pytest.raises(IntegrationError, match="distinct"):
        prepare_deployment([paths[0], paths[0]], snapshot, True)
    snapshot.write_text("preserve-existing-evidence")
    with pytest.raises(IntegrationError, match="fresh snapshot"):
        prepare_deployment(paths, snapshot, True)
    assert [path.read_bytes() for path in paths] == before
    assert snapshot.read_text() == "preserve-existing-evidence"


class SupportServer(ThreadingHTTPServer):
    """Expose only a bounded synthetic declaration, recording every read-only route."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        """Bind an ephemeral loopback port with no model, world or execution methods."""
        self.body = body
        self.status = status
        self.requests: list[str] = []
        super().__init__(("127.0.0.1", 0), SupportHandler)


class SupportHandler(BaseHTTPRequestHandler):
    """Serve declaration bytes; unexpected simulator or execution routes fail the test."""

    def do_GET(self) -> None:  # noqa: N802
        """Observe startup facts without any physical state mutation."""
        server: Any = self.server
        server.requests.append(self.path)
        assert self.path == "/v1/executions/recovery-support"
        self.send_response(server.status)
        self.send_header("Content-Length", str(len(server.body)))
        self.end_headers()
        self.wfile.write(server.body)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep local test metadata out of shared logs."""


@pytest.mark.parametrize("variant", ["valid", "mismatch", "malformed", "oversized", "http"])
def test_live_preflight_accepts_only_exact_bounded_adapter_support(
    tmp_path: Path, variant: str
) -> None:
    """A successful HTTP request alone is insufficient; exact support identity is required."""
    expected = execution_recovery_profile(shared_world=True, retain_stopped_session=True)
    body = json.dumps(expected).encode()
    if variant == "mismatch":
        body = json.dumps(execution_recovery_profile(shared_world=True)).encode()
    elif variant == "malformed":
        body = b"not-json"
    elif variant == "oversized":
        body = b" " * 65537
    server = SupportServer(body, 503 if variant == "http" else 200)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        paths = _copies(tmp_path)
        for path, port in zip(paths, (28100, 28102), strict=True):
            path.write_text(path.read_text().replace(f":{port}", f":{server.server_address[1]}"))
        snapshot = tmp_path / "deployment.json"
        prepare_deployment(paths, snapshot, True)
        before = copy.deepcopy(json.loads(snapshot.read_text()))
        if variant == "valid":
            verify_deployment(snapshot)
            assert len(server.requests) == 2
        else:
            with pytest.raises((IntegrationError, ValueError, OSError)):
                verify_deployment(snapshot)
        assert json.loads(snapshot.read_text()) == before
        assert all(path == "/v1/executions/recovery-support" for path in server.requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_source_byte_change_is_rejected_before_http(tmp_path: Path) -> None:
    """Even newline-only corruption cannot evade the exact startup file digest."""
    paths = _copies(tmp_path)
    snapshot = tmp_path / "deployment.json"
    prepare_deployment(paths, snapshot, True)
    paths[0].write_bytes(paths[0].read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(IntegrationError, match="source changed"):
        verify_deployment(snapshot)
