#!/usr/bin/env python3
"""Probe one Responses-compatible model while keeping credentials out of evidence."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    """Probe the configured Responses endpoint and save credential-free evidence."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: probe_model.py OUTPUT_DIR")
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=False)
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "")
    if not base_url or not api_key or not model:
        raise SystemExit("OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL are required")
    endpoint = base_url + "/responses"
    payload = {
        "model": model,
        "input": "Reply with exactly: MODEL_OK",
        "store": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 64,
    }
    (output / "request.json").write_text(
        json.dumps(
            {
                "endpoint": endpoint,
                "authorization_present": True,
                "payload": payload,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    except Exception as error:
        result = {
            "usable": False,
            "model": model,
            "elapsed_seconds": time.monotonic() - started,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 1
    elapsed = time.monotonic() - started
    (output / "raw-response.txt").write_text(body, encoding="utf-8")
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        decoded = None
    output_text = ""
    if isinstance(decoded, dict):
        for item in decoded.get("output", []):
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    output_text += str(part.get("text", ""))
    result = {
        "usable": status == 200
        and isinstance(decoded, dict)
        and decoded.get("status") == "completed",
        "http_status": status,
        "requested_model": model,
        "returned_model": decoded.get("model") if isinstance(decoded, dict) else None,
        "response_status": decoded.get("status") if isinstance(decoded, dict) else None,
        "output_text": output_text,
        "usage": decoded.get("usage") if isinstance(decoded, dict) else None,
        "elapsed_seconds": elapsed,
        "credential_recorded": False,
    }
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["usable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
