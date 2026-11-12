"""Nested JSON in actual host-rendered logs cannot bypass local feedback credential redaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from roboguide_workbench import feedback
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.client import ReadOnlyController
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.feedback import create_feedback
from roboguide_workbench.persistence import SessionStore
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_workbench import no_model
from test_workbench_feedback import contents, local_session
from test_workbench_v2 import ObservedController, mission_server


class NestedPlanner(FakePlanner):
    """Return a real valid plan carrying an opaque serialized adapter parameter."""

    def __init__(self, encoded_parameter: str) -> None:
        """Freeze the original adapter parameter without editing saved evidence."""
        super().__init__()
        self.encoded_parameter = encoded_parameter

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
        grounding_context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Use normal plan validation and place JSON inside an existing permitted string slot."""
        plan = super().plan(mission_id, grounded_intent, capability_catalog, grounding_context)
        raw = plan.to_json()
        tasks = cast(list[JSONObject], raw["tasks"])
        roles = cast(list[JSONObject], tasks[0]["roles"])
        execution = cast(JSONObject, roles[0]["execution"])
        parameters = cast(JSONObject, execution["parameters"])
        parameters["plan"] = self.encoded_parameter
        return MissionPlan.from_json(raw)


@pytest.mark.parametrize("layers", [0, 1, 2, 4])
@pytest.mark.parametrize("escaped_key", [False, True])
def test_generated_plan_and_raw_logs_redact_nested_json_credentials(
    tmp_path: Path, layers: int, escaped_key: bool
) -> None:
    """Production Mission HTTP/SQLite and host rendering retain useful data without leaked keys."""
    secret = 'opaque-adapter-key-123456789\\path"quoted-密码'
    encoded = json.dumps(
        {"api_key": secret, "description": "benign-adapter-description"}, ensure_ascii=False
    )
    if escaped_key:
        encoded = encoded.replace("api_key", r"api\u005fkey")
    for _ in range(layers):
        encoded = json.dumps(encoded, ensure_ascii=layers % 2 == 0)
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path / "mission", FakeInterpreter([_assessment()]), NestedPlanner(encoded), controller
    )
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "nested", {}, False)
        session = ConversationSession(
            ObservedController(),
            no_model,
            store,
            MissionActions(ReadOnlyController(endpoint), store),
        )
        try:
            generated = session.ask("/plan 只规划")
            session.ask("/raw")
            assert "AwaitingExecution" in generated
            assert session.shown_draft is not None and controller.submissions == []
            before = store.path.read_bytes()
            before_data = json.dumps(store.data, ensure_ascii=True, sort_keys=True)
            exported = contents(create_feedback(session))
            combined = b"\n".join(exported.values())
            assert b"opaque-adapter-key-123456789" not in combined
            assert b"benign-adapter-description" in exported["conversation.log"]
            assert b"benign-adapter-description" in exported["session.json"]
            assert b"[REDACTED]" in combined
            assert store.path.read_bytes() == before
            assert json.dumps(store.data, ensure_ascii=True, sort_keys=True) == before_data
            manifest = json.loads(exported["manifest.json"])
            assert manifest["redaction_count"] > 0 and manifest["local_only"] is True
        finally:
            store.close()


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_escaped_json_log_fragments_without_outer_quotes_are_sanitized(
    tmp_path: Path, layers: int
) -> None:
    """Explicitly encoded log fragments retain their encoding and benign fields after redaction."""
    session = local_session(tmp_path)
    text = json.dumps({"access_token": 'nested-token-value-abcdefghijkl\\"x', "ok": "retain-me"})
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    session.store.data["messages"] = [{"role": "assistant", "text": "event: " + text}]
    try:
        exported = contents(create_feedback(session))
        assert b"nested-token-value-abcdefghijkl" not in b"\n".join(exported.values())
        assert b"retain-me" in exported["conversation.log"]
        assert b"event:" in exported["conversation.log"]
    finally:
        session.store.close()


@pytest.mark.parametrize("label", ["api_key", "private_key", "ssh_private_key", "cookie"])
def test_malformed_encoded_credential_is_omitted_with_a_reason(tmp_path: Path, label: str) -> None:
    """A damaged escaped document is unavailable instead of retaining an unsafe original suffix."""
    session = local_session(tmp_path)
    session.store.data["messages"] = [
        {"role": "assistant", "text": 'event: {\\"' + label + '\\": \\"unsafe-secret-no-close"'}
    ]
    try:
        exported = contents(create_feedback(session))
        assert b"unsafe-secret-no-close" not in b"\n".join(exported.values())
        manifest = json.loads(exported["manifest.json"])
        assert any(item["reason"] == "encoded_json_unavailable" for item in manifest["omissions"])
        assert b"event:" in exported["conversation.log"]
    finally:
        session.store.close()


@pytest.mark.parametrize("limit", ["depth", "segments", "bytes"])
def test_nested_text_redaction_budgets_never_export_an_unprocessed_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Budget exhaustion is explicit and cannot expose a secret beyond the parsed prefix."""
    session = local_session(tmp_path)
    text = json.dumps({"api_key": "beyond-redaction-budget-value", "ok": "one"})
    if limit == "depth":
        for _ in range(8):
            text = json.dumps(text)
        monkeypatch.setattr(feedback, "MAX_DEPTH", 3)
    elif limit == "segments":
        text = '"harmless" ' * 5 + text
        monkeypatch.setattr(feedback, "MAX_JSON_TEXT_SEGMENTS", 3)
    else:
        text = "prefix " * 100 + text
        monkeypatch.setattr(feedback, "MAX_JSON_TEXT_BYTES", 128)
    session.store.data["messages"] = [{"role": "assistant", "text": text}]
    try:
        exported = contents(create_feedback(session))
        assert b"beyond-redaction-budget-value" not in b"\n".join(exported.values())
        manifest = json.loads(exported["manifest.json"])
        assert any("budget" in item["reason"] for item in manifest["omissions"])
    finally:
        session.store.close()


@pytest.mark.parametrize("unicode_quotes", [False, True])
def test_encoded_credential_field_without_container_is_sanitized(
    tmp_path: Path, unicode_quotes: bool
) -> None:
    """A logger's encoded key/value fragment stays safe without its original object braces."""
    session = local_session(tmp_path)
    fragment = json.dumps({"api_key": "fragment-key-abcdefghijkl", "ok": "keep-fragment"})[1:-1]
    fragment = json.dumps(fragment)[1:-1]
    if unicode_quotes:
        fragment = fragment.replace(r"\"", r"\u0022")
    session.store.data["messages"] = [{"role": "assistant", "text": "event: " + fragment}]
    try:
        exported = contents(create_feedback(session))
        assert b"fragment-key-abcdefghijkl" not in b"\n".join(exported.values())
        assert b"keep-fragment" in exported["conversation.log"]
    finally:
        session.store.close()


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_pretty_encoded_documents_do_not_leak_after_escaped_whitespace(
    tmp_path: Path, layers: int
) -> None:
    """Escaped indentation before an object's fields cannot hide its credentials."""
    session = local_session(tmp_path)
    text = json.dumps(
        {"api_key": "pretty-key-sentinel-abcdefghijkl", "ok": "retain-pretty"}, indent=2
    )
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    session.store.data["messages"] = [{"role": "assistant", "text": "event: " + text}]
    try:
        exported = contents(create_feedback(session))
        assert b"pretty-key-sentinel-abcdefghijkl" not in b"\n".join(exported.values())
        assert b"retain-pretty" in exported["conversation.log"]
    finally:
        session.store.close()


def test_redacted_embedded_json_retains_parseable_value_quoting(tmp_path: Path) -> None:
    """The diagnostic JSON copy retains useful structure after its credential values are removed."""
    session = local_session(tmp_path)
    session.store.data["messages"] = [
        {"role": "assistant", "text": json.dumps({"api_key": "quoted-key-value", "ok": "retained"})}
    ]
    try:
        exported = contents(create_feedback(session))
        message = json.loads(exported["session.json"])["messages"][0]["text"]
        assert json.loads(message) == {"api_key": "[REDACTED]", "ok": "retained"}
    finally:
        session.store.close()


@pytest.mark.parametrize("layers", [1, 2, 3, 4, 5])
@pytest.mark.parametrize(
    "label", ["private_key", "ssh_private_key", "client_key", "token", "cookie", "key"]
)
def test_encoded_pretty_json_uses_the_full_structured_credential_label_set(
    tmp_path: Path, layers: int, label: str
) -> None:
    """Escaped whitespace cannot force structured credentials into a weaker text fallback."""
    session = local_session(tmp_path)
    secret = "supported-private-key-sentinel-abcdefghijkl"
    text = json.dumps({label: secret, "ok": "keep-original-detail"}, indent=2)
    for _ in range(layers):
        text = json.dumps(text)[1:-1]
    session.store.data["messages"] = [{"role": "assistant", "text": "event: " + text}]
    session.store.save()
    before = session.store.path.read_bytes()
    try:
        exported = contents(create_feedback(session))
        assert secret.encode() not in b"\n".join(exported.values())
        assert b"keep-original-detail" in exported["conversation.log"]
        assert session.store.path.read_bytes() == before
    finally:
        session.store.close()


@pytest.mark.parametrize("label", ["password", "private_key", "client_secret", "token"])
@pytest.mark.parametrize("quote", ['"', "'"])
def test_escaped_quote_in_labeled_text_cannot_leave_a_credential_suffix(
    tmp_path: Path, label: str, quote: str
) -> None:
    """A credential with a quoted escape is redacted through its real closing delimiter."""
    session = local_session(tmp_path)
    secret = "supported-escaped-suffix-abcdefghijkl"
    text = "run " + label + "=" + quote + "first\\" + quote + secret + quote + " benign-argument"
    session.store.data["messages"] = [{"role": "assistant", "text": text}]
    session.store.save()
    before = session.store.path.read_bytes()
    try:
        exported = contents(create_feedback(session))
        assert secret.encode() not in b"\n".join(exported.values())
        assert b"benign-argument" in exported["conversation.log"]
        assert session.store.path.read_bytes() == before
    finally:
        session.store.close()


@pytest.mark.parametrize(
    "text",
    [
        'password="prefix spaced-private-sentinel',
        '{"api_key": "prefix spaced-private-sentinel',
        'run --password "prefix\\"spaced-private-sentinel" benign-argument',
        "run --token 'prefix\\'spaced-private-sentinel' benign-argument",
    ],
)
def test_incomplete_or_cli_quoted_credentials_do_not_leave_unredacted_suffixes(
    tmp_path: Path, text: str
) -> None:
    """Complete escaped values and incomplete line tails use the same quoted credential parser."""
    session = local_session(tmp_path)
    session.store.data["messages"] = [{"role": "assistant", "text": text + "\nnext benign line"}]
    try:
        exported = contents(create_feedback(session))
        assert b"spaced-private-sentinel" not in b"\n".join(exported.values())
        assert b"next benign line" in exported["conversation.log"]
    finally:
        session.store.close()


@pytest.mark.parametrize("prefix", ["password=", "--password "])
@pytest.mark.parametrize("quote", ['"', "'"])
def test_unclosed_quoted_credential_with_dangling_escape_is_redacted(
    tmp_path: Path, prefix: str, quote: str
) -> None:
    """A final literal backslash cannot make an incomplete password leak its spaced tail."""
    session = local_session(tmp_path)
    text = prefix + quote + "prefix spaced-sentinel-abcdefgh" + "\\" + "\r\nnext benign line"
    session.store.data["messages"] = [{"role": "assistant", "text": text}]
    session.store.save()
    before = session.store.path.read_bytes()
    try:
        exported = contents(create_feedback(session))
        assert b"spaced-sentinel-abcdefgh" not in b"\n".join(exported.values())
        assert b"next benign line" in exported["conversation.log"]
        assert session.store.path.read_bytes() == before
    finally:
        session.store.close()
