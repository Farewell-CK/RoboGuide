"""Private local feedback archives retain redacted inspectable evidence without external effects."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from mission.models import JSONObject
from roboguide_workbench import feedback
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.feedback import create_feedback
from roboguide_workbench.persistence import SessionStore
from test_workbench import ScriptedTransport, no_model, router
from test_workbench_v2 import ObservedController


def local_session(tmp_path: Path) -> ConversationSession:
    """Create a private session with no reachable service or configured model credential."""
    store = SessionStore(tmp_path / "sessions", "feedback", {}, False)
    reader = ObservedController()
    return ConversationSession(reader, no_model, store, MissionActions(reader, store))


def contents(path: Path) -> dict[str, bytes]:
    """Read exported members while enforcing fixed names and original private entry modes."""
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert all(stat.S_IMODE(item.external_attr >> 16) == 0o600 for item in archive.infolist())
        assert all("/" not in name and ".." not in name for name in archive.namelist())
        return {name: archive.read(name) for name in archive.namelist()}


def test_offline_feedback_retains_pending_receipts_without_modifying_sources(
    tmp_path: Path,
) -> None:
    """Export current local facts and real pending status without any service call."""
    session = local_session(tmp_path)
    try:
        session.ask("/nodes")
        session.store.data["commands"] = [
            {
                "state": "pending",
                "path": "/v1/mission-requests",
                "method": "POST",
                "idempotency_key": "exact-original-command",
                "request_digest": "sha256:original-body",
                "body": {"instruction": "检查东侧"},
            }
        ]
        session.store.save()
        original_data = json.dumps(session.store.data, sort_keys=True)
        original_bytes = session.store.path.read_bytes()
        destination = create_feedback(session)
        exported = contents(destination)
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
        assert destination.parent == session.store.path.parent / "feedback"
        assert set(exported) == {
            "session.json",
            "observations.json",
            "runtime.json",
            "conversation.log",
            "README.txt",
            "manifest.json",
        }
        manifest = json.loads(exported["manifest.json"])
        assert manifest["schema"] == "roboguide.feedback/v0.1" and manifest["local_only"] is True
        assert manifest["remote_logs"] == "not_collected" and manifest["omissions"] == []
        assert manifest["created_at"].endswith("+00:00")
        for name, record in manifest["entries"].items():
            assert record["sha256"] == hashlib.sha256(exported[name]).hexdigest()
            assert record["bytes"] == len(exported[name])
        original_command = json.loads(exported["session.json"])["commands"][0]
        assert original_command["state"] == "pending"
        assert original_command["idempotency_key"] == "exact-original-command"
        assert json.loads(exported["runtime.json"])["model"] == {"availability": "not_loaded"}
        assert "检查东侧" in exported["session.json"].decode("unicode_escape")
        assert session.store.path.read_bytes() == original_bytes
        assert json.dumps(session.store.data, sort_keys=True) == original_data
        assert session.router is None
    finally:
        session.store.close()


def test_feedback_redacts_structured_and_unstructured_credentials(tmp_path: Path) -> None:
    """Redact credentials in nested receipts, URLs, free text, PEM data and manifest paths."""
    session = local_session(tmp_path)
    supplied = "configured-private-credential-1234"
    known_key = "loaded-provider-secret-5678"
    fake_sk = "sk-example_private_key_0123456789"
    selected = router(ScriptedTransport([]))
    selected.key = known_key
    session.router = selected
    body: JSONObject = {
        "instruction": "仓库检查",
        "api_key": "nested-api-secret",
        "headers": {"Authorization": "Bearer nested-auth-secret", "Cookie": "session=private"},
        "github_token": "nested-token-secret",
        "ssh_private_key": "nested-ssh-secret",
        "source_url": "https://url-user:url-password@host.test/result?signature=url-signature#private",
        "text": (
            f"first {supplied}; second {known_key}; third {fake_sk}; "
            "Authorization: Bearer unlabelled-bearer-secret\n"
            'OPENAI_API_KEY="assignment-api-secret"\n'
            "password=assignment-password\n"
            "Authorization: Basic basic-auth-secret\n"
            "Cookie: session=free-cookie-secret\n"
            "run --api-key command-line-secret --token token-flag-secret\n"
            "-----BEGIN PRIVATE KEY-----\nprivate-key-body\n-----END PRIVATE KEY-----"
        ),
    }
    session.store.data["commands"] = [
        {"state": "pending", "idempotency_key": "original-key", "body": body}
    ]
    session.store.data["messages"] = [{"role": "user", "text": f"copied {supplied}"}]
    session.last_raw = {"original": body}
    session.store.save()
    before = session.store.path.read_bytes()
    try:
        exported = contents(create_feedback(session, secrets=[supplied]))
        combined = b"\n".join(exported.values()).decode()
        for secret in (
            supplied,
            known_key,
            fake_sk,
            "nested-api-secret",
            "nested-auth-secret",
            "session=private",
            "nested-token-secret",
            "nested-ssh-secret",
            "url-user",
            "url-password",
            "url-signature",
            "unlabelled-bearer-secret",
            "assignment-api-secret",
            "assignment-password",
            "basic-auth-secret",
            "free-cookie-secret",
            "command-line-secret",
            "token-flag-secret",
            "private-key-body",
        ):
            assert secret not in combined, secret
        clean = json.loads(exported["session.json"])["commands"][0]
        assert clean["idempotency_key"] == "original-key" and clean["state"] == "pending"
        assert clean["body"]["instruction"] == "仓库检查"
        assert "[REDACTED]" in combined
        assert json.loads(exported["manifest.json"])["redaction_count"] > 0
        assert session.store.path.read_bytes() == before
        assert body["api_key"] == "nested-api-secret"
    finally:
        session.store.close()


def test_feedback_marks_truncated_text_and_omitted_old_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded exports retain the recent tail and disclose omissions without changing history."""
    monkeypatch.setattr(feedback, "MAX_TEXT_BYTES", 128)
    monkeypatch.setattr(feedback, "MAX_LIST_ITEMS", 8)
    session = local_session(tmp_path)
    session.store.data["messages"] = [{"role": "user", "text": "x" * 4096}]
    session.store.data["commands"] = [
        {"state": "pending", "idempotency_key": f"original-{i}"} for i in range(12)
    ]
    session.store.save()
    before = session.store.path.read_bytes()
    try:
        exported = contents(create_feedback(session))
        document = json.loads(exported["session.json"])
        assert document["commands"][0]["idempotency_key"] == "original-4"
        assert document["commands"][-1]["idempotency_key"] == "original-11"
        assert document["messages"][0]["text"].endswith("[TRUNCATED]")
        omissions = json.loads(exported["manifest.json"])["omissions"]
        assert {row["reason"] for row in omissions} >= {"text_truncated", "oldest_items_omitted"}
        assert sum(len(raw) for raw in exported.values()) <= feedback.MAX_BUNDLE_BYTES
        assert session.store.path.read_bytes() == before
    finally:
        session.store.close()


def test_feedback_marks_oversized_entry_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry that remains too large is explicitly omitted rather than claiming complete logs."""
    monkeypatch.setattr(feedback, "MAX_ENTRY_BYTES", 256)
    session = local_session(tmp_path)
    session.store.data["messages"] = [{"role": "user", "text": "x" * 1024}]
    try:
        exported = contents(create_feedback(session))
        assert json.loads(exported["session.json"])["availability"] == "omitted"
        assert "conversation.log" not in exported
        omissions = json.loads(exported["manifest.json"])["omissions"]
        assert any(row["path"] == "session.json" for row in omissions)
    finally:
        session.store.close()


@pytest.mark.parametrize("destination", ["public", "symlink", "ancestor-symlink", "parent-jump"])
def test_feedback_refuses_unsafe_output_paths(tmp_path: Path, destination: str) -> None:
    """Output cannot follow symlinks, publish into a public directory, or traverse parent names."""
    session = local_session(tmp_path)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    if destination == "public":
        target.chmod(0o755)
        output = target
    elif destination == "parent-jump":
        output = target / ".." / "escape"
    else:
        link = tmp_path / "linked"
        link.symlink_to(target, target_is_directory=True)
        output = link if destination == "symlink" else link / "nested"
    try:
        with pytest.raises((ValueError, OSError)):
            create_feedback(session, output)
        assert not list(target.glob("*.zip"))
    finally:
        session.store.close()


def test_feedback_does_not_open_referenced_log_or_config_files(tmp_path: Path) -> None:
    """Only the owned session snapshot is collected, even when records mention other local files."""
    session = local_session(tmp_path)
    private = tmp_path / "server.log"
    private.write_text("unrelated-file-content-must-stay-private")
    session.last_raw = {"log_path": str(private)}
    session.store.data["messages"] = [{"role": "user", "text": "see " + str(private)}]
    try:
        exported = contents(create_feedback(session))
        assert b"unrelated-file-content-must-stay-private" not in b"\n".join(exported.values())
        assert "server.log" not in exported
    finally:
        session.store.close()


def test_failed_feedback_publication_leaves_no_archive_or_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted ZIP write publishes no partial archive and leaves original state intact."""
    session = local_session(tmp_path)
    before = session.store.path.read_bytes()

    def fail(*args: object, **kwargs: object) -> None:
        """Fail after a private temporary file exists but before any archive can be published."""
        raise OSError("injected feedback write failure")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail)
    output = tmp_path / "bundles"
    try:
        with pytest.raises(OSError, match="write failure"):
            create_feedback(session, output)
        assert list(output.iterdir()) == []
        assert session.store.path.read_bytes() == before
    finally:
        session.store.close()


def test_feedback_creates_distinct_private_archives(tmp_path: Path) -> None:
    """Repeated explicit exports cannot overwrite the earlier diagnostic evidence."""
    session = local_session(tmp_path)
    try:
        first = create_feedback(session)
        before = first.read_bytes()
        second = create_feedback(session)
        assert first != second and first.read_bytes() == before
        assert stat.S_IMODE(first.stat().st_mode) == stat.S_IMODE(second.stat().st_mode) == 0o600
        assert first.stat().st_uid == os.getuid()
    finally:
        session.store.close()
