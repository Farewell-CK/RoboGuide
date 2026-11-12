"""Exact-plan read-only deployment checks through the existing Controller assessment ports."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import cast

from mission.deployment_assessment import InitialOperationAssessment
from mission.models import JSONObject
from mission.plan_assessment import validate_assessment
from mission.plan_validation import PlanValidationError
from mission.request_record import MissionRequestRecord

from roboguide_workbench.client import ReadOnlyController, WorkbenchError, request_json
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.views import safe_text


def check_plan(connection: ReadOnlyController, record: MissionRequestRecord) -> ReadResult:
    """Inspect original plan bytes without admission, a fresh execution grant or command replay."""
    if record.plan is None:
        raise WorkbenchError("当前 Request 没有完整计划，请先 /request。")
    plan = record.plan
    body = json.dumps(plan.to_json(), ensure_ascii=False, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    if connection.tenant:
        headers["X-RoboGuide-Tenant"] = connection.tenant
    components: list[JSONObject] = []
    deadline = time.monotonic() + connection.timeout
    for label, path in (
        ("已发布合同", "/v1/missions/validate-plan"),
        ("初始部署支持", "/v1/missions/assess-initial-support"),
    ):
        component: JSONObject = {"source": label, "complete": False}
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkbenchError("检查预算已耗尽。")
            raw = request_json(
                connection.endpoint + path, "POST", headers, body, remaining, connection.context
            )
            if label == "已发布合同":
                try:
                    validate_assessment(raw, plan)
                except PlanValidationError:
                    pass  # Typed blocked feedback is a complete observation, never permission.
                decision = cast(str, raw["decision"])
            else:
                assessment = InitialOperationAssessment.from_json(raw)
                if not assessment.matches_plan(plan):
                    raise WorkbenchError("部署检查与当前计划不匹配。")
                decision = assessment.decision
            component.update(
                {"complete": decision != "unavailable", "decision": decision, "response": raw}
            )
        except (ValueError, OSError, TypeError, KeyError) as error:
            component["error"] = safe_text(str(error))
        component["received_at"] = datetime.now(UTC).isoformat()
        components.append(component)
    lines = [
        f"执行前检查 · Request {record.request_id} · 版本 {record.draft_revision}",
        "摘要：" + str(record.draft_digest),
    ]
    for component in components:
        observed_decision = component.get("decision", "unavailable")
        labels = {
            "not_blocked": "本次检查未发现阻塞",
            "blocked": "存在已知阻塞",
            "unavailable": "证据不可用，条件未知",
        }
        lines.append(str(component["source"]) + "：" + labels.get(str(observed_decision), "未知"))
        detail = component.get("response", component.get("error"))
        lines.append(safe_text(json.dumps(detail, ensure_ascii=False)))
    lines.append(
        "这是分别读取的检查快照；未提交任务，也不授予执行许可。实际执行仍由 Control 检查。"
    )
    complete = all(c["complete"] is True for c in components)
    return ReadResult("\n".join(lines), complete, tuple(components))
