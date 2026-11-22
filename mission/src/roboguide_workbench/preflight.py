"""Exact-plan read-only deployment checks through the existing Controller assessment ports."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import cast

from mission.deployment_assessment import InitialOperationAssessment
from mission.models import JSONObject
from mission.plan_assessment import TIMING_PREVIEW_SCHEMA, validate_assessment
from mission.plan_validation import PlanValidationError
from mission.request_record import MissionRequestRecord

from roboguide_workbench.client import ReadOnlyController, WorkbenchError, request_json
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.views import safe_text

_STATUS_LABELS = {
    "not_blocked": "本次检查未发现阻塞",
    "blocked": "存在已知阻塞",
    "unavailable": "证据不可用，条件未知",
}
_EXCLUSION_LABELS = {
    "registration_missing": "没有登记",
    "identity_unavailable": "身份不可用",
    "decision_evidence_unavailable": "决策依据缺失",
    "health_unschedulable": "健康状态不满足",
    "status_stale": "状态报告过期",
    "liveness_unreachable": "节点不可达",
    "lease_inactive": "租约未激活",
    "role_contract_unavailable": "角色合同缺失",
    "operation_unsupported": "不支持所需操作",
    "deployment_restriction": "当前部署不允许",
    "actor_contract_unavailable": "执行设备合同不可用",
}


def _blockers(response: object) -> list[str]:
    """Describe only validated exact-plan issues and bounded deployment evidence."""
    if not isinstance(response, dict):
        return []
    if response.get("schema_version") == TIMING_PREVIEW_SCHEMA:
        response = response.get("assessment")
        if not isinstance(response, dict):
            return []
    issues = response.get("issues")
    if isinstance(issues, list):
        rows = [
            f"{safe_text(str(row['path']))[:100]} · {safe_text(str(row['message']))[:160]}"
            for row in issues[:3]
            if isinstance(row, dict)
        ]
        if len(issues) > 3 or response.get("issues_truncated") is True:
            rows.append("其余问题见检查详情。")
        return rows
    if response.get("decision") != "blocked":
        return []
    assessment = InitialOperationAssessment.from_json(response)
    diagnostics = {(item.task_id, item.role_id): item for item in assessment.candidate_diagnostics}
    rows = []
    for role in assessment.roles:
        if role.scoped_disjoint_count <= 0:
            continue
        line = (
            f"{safe_text(role.task_id)[:100]} / {safe_text(role.role_id)[:100]} · "
            f"{safe_text(role.operation)[:120]} 的已核对候选不满足部署条件"
        )
        diagnostic = diagnostics.get((role.task_id, role.role_id))
        if diagnostic is not None:
            reasons = [
                _EXCLUSION_LABELS[key]
                for key, count in diagnostic.exclusions
                if key in _EXCLUSION_LABELS and count > 0
            ]
            if reasons:
                line += "（" + "、".join(reasons) + "）"
        rows.append(line)
        if len(rows) == 3:
            break
    return rows


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
        ("已发布合同", "/v1/missions/validate-plan?timing=v0.1"),
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
                assessment_body = (
                    cast(JSONObject, raw["assessment"])
                    if raw.get("schema_version") == TIMING_PREVIEW_SCHEMA
                    else raw
                )
                decision = cast(str, assessment_body["decision"])
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
    summary = lines.copy()
    for component in components:
        decision = str(component.get("decision", "unavailable"))
        headline = str(component["source"]) + "：" + _STATUS_LABELS.get(decision, "未知")
        lines.append(headline)
        summary.append(headline)
        if decision == "blocked":
            summary.extend("· " + blocker for blocker in _blockers(component.get("response")))
        if decision == "unavailable":
            summary.append("· 该项缺少可用证据。")
        detail = component.get("response", component.get("error"))
        lines.append(safe_text(json.dumps(detail, ensure_ascii=False)))
    lines.append(
        "这是分别读取的检查快照；未提交任务，也不授予执行许可。实际执行仍由 Control 检查。"
    )
    complete = all(c["complete"] is True for c in components)
    if any(item.get("decision") == "blocked" for item in components):
        summary.append("下一步：补齐上面的条件或修改计划，再检查新版本。")
    elif not complete:
        summary.append("下一步：核对不可用的检查来源。")
    else:
        summary.append("检查已完成，执行仍由 Control 核验并需要明确执行指令。")
    return ReadResult("\n".join(lines), complete, tuple(components), summary="\n".join(summary))
