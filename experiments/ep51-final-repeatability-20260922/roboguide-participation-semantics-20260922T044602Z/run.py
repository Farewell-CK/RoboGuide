"""Run a preregistered Interpreter-only participation-semantics validation."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, cast

ROOT = Path(os.environ["PARTICIPATION_STUDY_ROOT"])
REPO = Path("/tmp/roboguide-stage2-execution-contract")
EXPECTED_HEAD = "219ba23ca454d11bcb718a9bbf09e72a86cab61c"
sys.path.insert(0, str(REPO / "mission/src"))

from mission.config import load_settings
from mission.grounding_reader import EmptyMissionGroundingReader
from mission.models import JSONObject
from mission.request_record import DialogueSpeaker, DialogueTurn, DialogueTurnKind
from mission.responses import ResponsesMissionInterpreter, UrllibJsonTransport

CASES = {
    "available": (
        "Three suitable mobile robots are available for this mission. Inspect storage zone alpha "
        "and storage zone beta. Decide how to divide the work."
    ),
    "minimum": (
        "At least two mobile robots must participate in this mission. Inspect storage zone alpha "
        "and storage zone beta. Decide how to divide the work while preserving that participation "
        "requirement."
    ),
    "universal": (
        "All three available mobile robots must participate in this mission. Inspect storage zone "
        "alpha and storage zone beta. Decide how to divide the work while preserving that "
        "participation requirement."
    ),
}
ORDER = tuple(case for _ in range(3) for case in ("available", "minimum", "universal"))


def canonical_bytes(value: object) -> bytes:
    """Encode public JSON evidence deterministically."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(data: bytes) -> str:
    """Return a prefixed SHA-256 identity for public evidence bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sanitize(value: object, secret: str) -> object:
    """Remove the in-memory credential from recursively persisted Provider evidence."""
    if isinstance(value, str):
        return re.sub(
            r"sk-[A-Za-z0-9_-]{12,}",
            "[REDACTED_CREDENTIAL]",
            value.replace(secret, "[REDACTED_CREDENTIAL]"),
        )
    if isinstance(value, list):
        return [sanitize(item, secret) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize(item, secret) for key, item in value.items()}
    return value


def write_json(path: Path, value: object) -> None:
    """Write one immutable evidence object and refuse accidental overwrite."""
    with path.open("xb") as stream:
        stream.write(canonical_bytes(value))


class EvidenceTransport:
    """Capture the exact nonsecret request and raw response around production HTTP transport."""

    def __init__(self, attempt_dir: Path, secret: str) -> None:
        """Bind one transport instance to one immutable attempt directory."""
        self._attempt_dir = attempt_dir
        self._secret = secret
        self._delegate = UrllibJsonTransport()

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JSONObject,
        timeout_seconds: float,
    ) -> JSONObject:
        """Persist redacted request/response evidence without retries or header disclosure."""
        started = time.monotonic()
        write_json(
            self._attempt_dir / "provider-request.json",
            {
                "url": url,
                "timeout_seconds": timeout_seconds,
                "payload": sanitize(copy.deepcopy(payload), self._secret),
                "authorization_present": "Authorization" in headers,
            },
        )
        try:
            response = self._delegate.post_json(url, headers, payload, timeout_seconds)
        except Exception as error:
            write_json(
                self._attempt_dir / "provider-error.json",
                {
                    "elapsed_seconds": time.monotonic() - started,
                    "type": type(error).__name__,
                    "message": sanitize(str(error), self._secret),
                },
            )
            raise
        write_json(
            self._attempt_dir / "provider-response.json",
            {
                "elapsed_seconds": time.monotonic() - started,
                "response": sanitize(copy.deepcopy(response), self._secret),
            },
        )
        return response


def dialogue_for(case: str) -> tuple[DialogueTurn, ...]:
    """Build one stable single-turn input for a registered semantic case."""
    return (
        DialogueTurn(
            turn_id=f"turn-{case}",
            speaker=DialogueSpeaker.USER,
            kind=DialogueTurnKind.INSTRUCTION,
            content=CASES[case],
            created_at_ms=1,
        ),
    )


def main() -> None:
    """Validate exact code identity, execute nine calls, and archive parsed assessments."""
    head = subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"], text=True)
    if head != EXPECTED_HEAD or dirty:
        raise RuntimeError(f"study code identity mismatch: head={head!r}, dirty={bool(dirty)}")
    secret = os.environ.get("OPENAI_API_KEY", "")
    if not secret:
        raise RuntimeError("OPENAI_API_KEY is unavailable")
    settings = load_settings(REPO / "config/mission.toml", repository_root=REPO)
    prompt = settings.prompts.interpreter_path.read_bytes()
    manifest = {
        "schema": "roboguide.participation-semantics-study/v0.1",
        "code_head": head,
        "component": "ResponsesMissionInterpreter only",
        "services_started": [],
        "mission_requests_created": 0,
        "control_node_habitat_started": False,
        "prompt_path": str(settings.prompts.interpreter_path),
        "prompt_sha256": digest(prompt),
        "model": settings.llm.model,
        "reasoning_effort": settings.llm.reasoning_effort,
        "max_output_tokens": settings.llm.max_output_tokens,
        "timeout_seconds": settings.llm.timeout_seconds,
        "provider_name": settings.provider.name,
        "endpoint": settings.provider.endpoint({"ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"}),
        "credential_archived": False,
        "order": list(ORDER),
        "calls_registered": len(ORDER),
        "retries": 0,
        "case_inputs": CASES,
        "interpretation_note": (
            "This bounded study checks the production Interpreter on three generic participation "
            "semantics. It does not prove Planner, Reviewer, Control, or physical execution behavior."
        ),
    }
    write_json(ROOT / "manifest.json", manifest)
    results = []
    for index, case in enumerate(ORDER, start=1):
        attempt_dir = ROOT / f"attempt-{index:02d}-{case}"
        attempt_dir.mkdir()
        dialogue = dialogue_for(case)
        grounding = EmptyMissionGroundingReader().capture(f"request-{case}", dialogue, 10)
        transport = EvidenceTransport(attempt_dir, secret)
        interpreter = ResponsesMissionInterpreter(
            settings,
            {"OPENAI_API_KEY": secret, "ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP": "1"},
            transport,
        )
        started = time.monotonic()
        assessment = interpreter.interpret(dialogue, grounding)
        result = {
            "attempt": index,
            "case": case,
            "elapsed_seconds": time.monotonic() - started,
            "dialogue": [turn.to_json() for turn in dialogue],
            "grounding_context": grounding.to_json(),
            "assessment": assessment.to_json(),
        }
        write_json(attempt_dir / "result.json", result)
        results.append(result)
        print(json.dumps({"attempt": index, "case": case, "assessment": assessment.to_json()}, ensure_ascii=False), flush=True)
    write_json(ROOT / "results.json", results)
    checksums = {}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.json":
            checksums[str(path.relative_to(ROOT))] = digest(path.read_bytes())
    write_json(ROOT / "SHA256SUMS.json", checksums)


if __name__ == "__main__":
    main()
