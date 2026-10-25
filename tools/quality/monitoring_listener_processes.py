"""Require native capacity alerts from actual owned listener pressure, never synthetic metrics."""

from __future__ import annotations

import urllib.parse
from typing import Any

from check_state_verifier_processes import save, wait_for
from monitoring_processes import MonitoringProcesses


class ListenerMonitoring(MonitoringProcesses):
    """Keep actual native alert messages separate from long-term SLO and command authority."""

    def observe_capacity(self, state: str, expected: set[str]) -> dict[str, Any]:
        """Require each original alert's real firing or resolved notification and native query."""
        assert self.receiver is not None

        def received() -> list[dict[str, Any]] | None:
            """Inspect only independently received Alertmanager messages at this owned endpoint."""
            assert self.receiver is not None
            rows = self.receiver.snapshot()
            names = {
                alert.get("labels", {}).get("alertname")
                for message in rows
                for alert in message.get("alerts", [])
                if alert.get("status") == state
            }
            return rows if expected <= names else None

        messages = wait_for(received, "actual listener capacity " + state, seconds=20)
        expression = "|".join(sorted(expected))
        query = urllib.parse.urlencode({"query": f'ALERTS{{alertname=~"{expression}"}}'})
        record = {
            "state": state,
            "expected_alerts": sorted(expected),
            "actual_messages": messages,
            "actual_native_query": self.api("/api/v1/query?" + query),
            "slo_achieved": False,
        }
        save(self.root / ("listener-" + state + ".json"), record)
        return record
