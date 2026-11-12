"""Display local chat context without turning connection configuration into liveness proof."""

from __future__ import annotations

from roboguide_workbench.views import safe_text
from roboguide_workbench.workspace import WorkbenchSession


def status_line(session: WorkbenchSession, *, busy: bool = False) -> str:
    """Report actual local scope and selections, marking backend liveness as unobserved."""
    model = getattr(session, "model_label", "未加载")
    if session.router is not None and hasattr(session.router, "settings"):
        model = session.router.settings.llm.model
    lifecycle = session.shown_draft.get("lifecycle") if session.shown_draft else None
    pending = (
        "取消待确认"
        if session.pending_cancel
        else {
            "AwaitingExecution": "执行待授权",
            "AwaitingApproval": "等待风险审批",
            "Accepted": "Request 已接纳",
        }.get(str(lifecycle), "")
    )
    return safe_text(
        f"{session.controller.endpoint} | 身份 {session.controller.tenant or '服务端判定'}"
        f" | 模型 {model} | 会话 {session.store.path.stem}"
        f" | Request {session.request_id or '-'} | Mission {session.last_mission or '-'}"
        f" | {'处理中' if busy else '空闲'} {pending} | 服务在线状态未探测"
    )
