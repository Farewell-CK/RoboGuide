"""Offline usage-isolation tests: preserve client calls, responses, history and real errors."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from habitat_local_eaios import native_reset_observer, native_workload  # noqa: E402
from habitat_local_eaios.native_usage_observer import install_usage_observer  # noqa: E402


class UsageClient:
    """Expose the existing added callback boundary, with an inert once-only Provider response."""

    def __init__(self, path: Path, response: Any) -> None:
        """Keep response identity, existing logging flags and original request/history counters."""
        self.token_usage = 5
        self.enable_logging = True
        self.usage_details_file = str(path)
        self.agent_name = "agent_test"
        self.model = "fixture-model"
        self.response = response
        self.requests: list[dict[str, Any]] = []
        self.history: list[Any] = []
        self.provider_error: Exception | None = None

    def _record_usage(self, response: Any, started_at: float) -> None:
        """Represent the unsafe added logging callback, keeping required accounting first."""
        self.token_usage += response.usage.total_tokens
        if self.enable_logging and self.usage_details_file:
            with open(self.usage_details_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(response.usage.prompt_tokens_details))

    def chat(self, request: dict[str, Any]) -> Any:
        """Return one precomputed Provider message unchanged, with no real network client."""
        self.requests.append(request)
        if self.provider_error is not None:
            raise self.provider_error
        self._record_usage(self.response, time.time())
        message = self.response.choices[0].message
        self.history.append(message)
        return message


def _response(details: Any = None) -> Any:
    """Supply official usage and a tool selection without an SDK, model or simulator."""
    return SimpleNamespace(
        usage=SimpleNamespace(
            total_tokens=12,
            prompt_tokens=8,
            completion_tokens=4,
            prompt_tokens_details=details,
            completion_tokens_details=None,
        ),
        choices=[SimpleNamespace(message={"tool": "navigate", "target": "assigned-destination"})],
    )


@pytest.mark.parametrize(
    "fault", [None, "details-read", "json", "directory", "missing-parent", "write"]
)
def test_optional_usage_fault_keeps_request_response_history_and_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    """One valid response survives optional reads/serialization/storage; no call is retried."""
    from habitat_local_eaios import native_usage_observer

    path = tmp_path / "usage.jsonl"
    response = _response(object() if fault == "json" else {"cached_tokens": 3})
    if fault == "details-read":

        class UnreadableUsage:
            """Keep required tokens available while an optional detail accessor fails."""

            total_tokens, prompt_tokens, completion_tokens = 12, 8, 4
            completion_tokens_details = None

            @property
            def prompt_tokens_details(self) -> Any:
                """Fail only diagnostic access without changing the returned Provider message."""
                raise OSError("private error detail")

        response.usage = UnreadableUsage()
    elif fault == "directory":
        path.mkdir()
    elif fault == "missing-parent":
        path = tmp_path / "absent" / "usage.jsonl"
    elif fault == "write":

        def broken_open(*args: Any, **kwargs: Any) -> Any:
            """Fail only the added JSONL writer, leaving status persistence available."""
            raise OSError("private write detail")

        monkeypatch.setattr(native_usage_observer, "open", broken_open, raising=False)
    original_chat = UsageClient.chat
    original_callback = UsageClient._record_usage
    restore = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder), tmp_path
    )
    client = UsageClient(path, response)
    request = {
        "messages": [{"role": "user", "content": "execute assigned operation"}],
        "model": "fixture-model",
        "tools": [{"name": "navigate"}],
        "tool_choice": "required",
    }
    try:
        assert UsageClient.chat is original_chat
        for expected_total in (17, 29):
            result = client.chat(request)
            assert result is response.choices[0].message
            assert client.requests[-1] is request and client.history[-1] is result
            assert client.token_usage == expected_total
        assert len(client.requests) == len(client.history) == 2
        unavailable = tmp_path / "usage-observer-unavailable.json"
        if fault is None:
            assert not unavailable.exists()
            records = [json.loads(line) for line in path.read_text().splitlines()]
            assert len(records) == 2
            assert records[0]["usage"] == {
                "prompt_tokens": 8,
                "completion_tokens": 4,
                "total_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": None,
            }
            assert records[0]["model"] == client.model
        else:
            status = json.loads(unavailable.read_text())
            assert status["status"] == "unavailable" and status["failed_records"] == 2
            assert status["last_error_type"] in {
                "OSError",
                "TypeError",
                "IsADirectoryError",
                "FileNotFoundError",
            }
            assert "private" not in unavailable.read_text()
    finally:
        restore()
    assert UsageClient._record_usage is original_callback


@pytest.mark.parametrize("fault", ["provider", "required-usage", "response-decode"])
def test_original_client_errors_are_not_swallowed(tmp_path: Path, fault: str) -> None:
    """Provider, required accounting and original decoding stay outside optional isolation."""
    response = _response()
    client = UsageClient(tmp_path / "usage.jsonl", response)
    error = RuntimeError("original Provider error")
    if fault == "provider":
        client.provider_error = error
        expected: type[Exception] = RuntimeError
    elif fault == "required-usage":
        response.usage.total_tokens = None
        expected = TypeError
    else:
        response.choices = []
        expected = IndexError
    restore = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder), tmp_path
    )
    try:
        with pytest.raises(expected) as caught:
            client.chat({"unchanged": True})
        if fault == "provider":
            assert caught.value is error
        assert len(client.requests) == 1 and client.history == []
        assert not (tmp_path / "usage-observer-unavailable.json").exists()
    finally:
        restore()


def test_lost_failure_status_does_not_mask_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second storage failure preserves the response; missing evidence remains unknown."""
    from habitat_local_eaios import native_usage_observer

    def broken_status(*args: Any, **kwargs: Any) -> None:
        """Simulate unavailable sidecar storage with no model or physical effect."""
        raise OSError("status storage unavailable")

    monkeypatch.setattr(native_usage_observer, "write_text_atomic", broken_status)
    response = _response(object())
    client = UsageClient(tmp_path / "usage.jsonl", response)
    restore = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder), tmp_path
    )
    try:
        assert client.chat({}) is response.choices[0].message
        assert client.token_usage == 17 and len(client.requests) == 1
        assert not (tmp_path / "usage-observer-unavailable.json").exists()
    finally:
        restore()


@pytest.mark.parametrize("disable", ["flag", "empty-path"])
def test_disabled_detail_logging_only_counts_original_tokens(tmp_path: Path, disable: str) -> None:
    """Logging-off conditions write no detail or loss file and preserve official totals."""
    client = UsageClient(tmp_path / "usage.jsonl", _response(object()))
    if disable == "flag":
        client.enable_logging = False
    else:
        client.usage_details_file = ""
    restore = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder), tmp_path
    )
    try:
        assert client.chat({}) is client.response.choices[0].message
        assert client.token_usage == 17 and list(tmp_path.iterdir()) == []
    finally:
        restore()


def test_legacy_client_and_later_hook_ownership_are_preserved(tmp_path: Path) -> None:
    """An original client without added instrumentation remains untouched; restoration is scoped."""
    legacy = SimpleNamespace(OpenAIModel=type("LegacyClient", (), {}))
    restore_legacy = install_usage_observer(legacy, tmp_path)
    assert not hasattr(legacy.OpenAIModel, "_record_usage")
    restore_legacy()
    restore = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder), tmp_path
    )
    installed = UsageClient._record_usage
    client_type: Any = UsageClient
    try:
        client_type._record_usage = legacy.OpenAIModel
        restore()
        assert UsageClient._record_usage is legacy.OpenAIModel
    finally:
        client_type._record_usage = installed
        restore()


def test_existing_vendor_encoder_handles_sdk_detail_objects(tmp_path: Path) -> None:
    """Keep the supplied vendor encoder rather than discarding otherwise serializable SDK usage."""

    class Detail:
        """Represent an SDK object with an admitted vendor serialization path."""

        cached_tokens = 3

    class VendorEncoder(json.JSONEncoder):
        """Support the original client's structured usage object without importing an SDK."""

        def default(self, value: Any) -> Any:
            """Use the vendor's exact detail representation, rejecting other unknown objects."""
            if isinstance(value, Detail):
                return {"cached_tokens": value.cached_tokens}
            return super().default(value)

    path = tmp_path / "usage.jsonl"
    client = UsageClient(path, _response(Detail()))
    restore = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=VendorEncoder), tmp_path
    )
    try:
        assert client.chat({}) is client.response.choices[0].message
        record = json.loads(path.read_text())
        assert record["usage"]["prompt_tokens_details"] == {"cached_tokens": 3}
        assert not (tmp_path / "usage-observer-unavailable.json").exists()
    finally:
        restore()


def test_stage2_close_error_preserves_original_exception_and_restores_logging(
    tmp_path: Path,
) -> None:
    """Physical cleanup errors still propagate while the process-local observer is removed."""
    from habitat_local_eaios.emos_stage2 import EmosStage2Runtime

    runtime = EmosStage2Runtime(SimpleNamespace(evidence_dir=tmp_path))
    original_callback = UsageClient._record_usage
    error = RuntimeError("original physical close failure")

    def original_close() -> None:
        """Raise the exact original physical cleanup error without calling any simulator."""
        raise error

    runtime._gym_env = SimpleNamespace(close=original_close)
    runtime._restore_usage_observer = install_usage_observer(
        SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder), tmp_path
    )
    with pytest.raises(RuntimeError) as caught:
        runtime.close()
    assert caught.value is error
    assert UsageClient._record_usage is original_callback


@pytest.mark.parametrize("entry", [native_workload, native_reset_observer])
@pytest.mark.parametrize("original_failure", [False, True])
def test_native_entries_install_and_restore_usage_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: Any, original_failure: bool
) -> None:
    """Actual CLI wrappers protect the original evaluator once and restore after either result."""
    calls: list[str] = []
    factory = SimpleNamespace(make_gym_from_config=object())
    original_factory, original_argv = factory.make_gym_from_config, sys.argv
    original_callback = UsageClient._record_usage
    models = SimpleNamespace(OpenAIModel=UsageClient, CustomJSONEncoder=json.JSONEncoder)
    output = tmp_path / "new-output"
    error = RuntimeError("original evaluator failure")

    def import_module(name: str) -> Any:
        """Load only inert production-boundary objects, never the real native dependencies."""
        return {
            "habitat_baselines.common.habitat_env_factory": factory,
            "habitat_mas.utils.models": models,
        }[name]

    def evaluate(name: str, **kwargs: Any) -> None:
        """Exercise logging loss inside the installed wrapper without creating an environment."""
        calls.append(name)
        assert UsageClient._record_usage is not original_callback
        client = UsageClient(output / "absent" / "usage.jsonl", _response())
        assert client.chat({}) is client.response.choices[0].message
        assert len(client.requests) == 1
        if original_failure:
            raise error

    monkeypatch.setattr(entry.importlib, "import_module", import_module)
    monkeypatch.setattr(entry.runpy, "run_module", evaluate)
    if entry is native_workload:
        monkeypatch.setattr(entry, "build_runtime_source_manifest", lambda names: {})
    arguments = ["--evidence-dir", str(output), "--run-id", "test-run", "--episode-id", "7"]
    if entry is native_workload:
        arguments += [
            "--scene-id",
            "scene",
            "--dataset-path",
            str(tmp_path / "data"),
            "--dataset-sha256",
            "a" * 64,
        ]
    arguments += ["--", "--config-name=original.yaml"]
    if original_failure:
        with pytest.raises(RuntimeError) as caught:
            entry.main(arguments)
        assert caught.value is error
    else:
        entry.main(arguments)
    assert calls == ["habitat_baselines.run"]
    assert UsageClient._record_usage is original_callback
    assert factory.make_gym_from_config is original_factory and sys.argv is original_argv
