"""Keep external-attempt incidents separate from process and evidence authority."""

from __future__ import annotations

import math
import os
import signal
import subprocess
from collections.abc import Iterable

from roboguide_eval.models import JSONObject


def provider_incidents(
    observations: Iterable[JSONObject],
    *,
    expected_model: str,
    continue_http_statuses: frozenset[int] = frozenset(),
) -> list[JSONObject]:
    """Retain HTTP incidents while leaving declared transient statuses to the original client.

    A continued incident proves neither retry success nor an official outcome. Model identity
    is still checked on every successful response, including a client's subsequent retry.
    This function never sends a request, retries an operation or changes a response.
    """
    if not expected_model or any(
        type(status) is not int or not 400 <= status <= 599 for status in continue_http_statuses
    ):
        raise ValueError("invalid Provider observation policy")
    incidents: list[JSONObject] = []
    for row in observations:
        common: JSONObject = {"channel": row.get("channel"), "sequence": row.get("seq")}
        status = row.get("status")
        if type(status) is not int or status != 200:
            incidents.append(
                {
                    **common,
                    "kind": "provider-http-or-transport",
                    "status": status,
                    "requires_stop": type(status) is not int
                    or status not in continue_http_statuses,
                }
            )
        elif row.get("response_model") != expected_model:
            incidents.append(
                {
                    **common,
                    "kind": "model-identity-unconfirmed",
                    "actual_model": row.get("response_model"),
                    "requires_stop": True,
                }
            )
    return incidents


def terminate_owned_session(
    child: subprocess.Popen[bytes], *, grace_seconds: float = 120, kill_seconds: float = 10
) -> bool:
    """Signal the owned supervisor first so it can archive before terminating its children.

    The supervisor must own cleanup of its children; uncooperative wrappers must use
    their existing group-stop path. Return whether group-wide escalation was required.
    Only a still-live session leader created by the caller may receive the final group
    signal. An already exited child is never signalled.
    """
    if any(not math.isfinite(value) or value <= 0 for value in (grace_seconds, kill_seconds)):
        raise ValueError("termination budgets must be finite and positive")
    if child.poll() is not None:
        return False
    try:
        child.terminate()
        child.wait(timeout=grace_seconds)
        return False
    except ProcessLookupError:
        child.wait(timeout=kill_seconds)
        return False
    except subprocess.TimeoutExpired:
        if child.poll() is not None:
            return False
        try:
            if os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
        except ProcessLookupError:
            pass
        child.wait(timeout=kill_seconds)
        return True
