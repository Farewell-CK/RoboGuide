# ADR-0259 · 任务依赖和截止时间前检

会师的最后一次拍照在 Mission 准入后 156.549 秒才满足前置依赖，错过了从准入起算的 120 秒完成窗口。Controller 的任务详情曾只显示 `Ready`，现场需要查看调度事件才能知道自动调度已经停止。

Orchestration 沿 DAG 计算各任务的最早开始下界。存在多个前序时取最大值。前序的最早开始时间已经超过当前任务的最晚开始或完成截止时间时，声明构成矛盾。新提交和前检共享这条规则，时间相等仍符合窗口。前检返回现有的 `deployment.contract_incompatible` 及实际计划的字段路径。已提交 Mission 的控制状态和原始接受时间继续由 Control 和 Orchestration 持有。

默认 `POST /v1/missions/validate-plan` 返回既有合同。带 `?timing=v0.1` 的请求保留原计划请求体，响应为 `roboguide.plan-timing-preview/v0.1`。其中 `assessment` 是原有的、绑定计划摘要的检查结果，`timing` 列出时间窗诊断。MI 和工作台支持两种回复。

需要检查历史样本时使用 `?timing=history-v0.1`，请求体包含 `schema_version: roboguide.plan-timing-history/v0.1`、原 `plan` 对象、以及 `estimates` 数组。每条估时包含 `task_id`、`duration_ms`、`sample_count` 和 `source_digest`。时间覆盖从任务可开始到满足所有完成证据的等待。估时按调用者提供的场景标注，原始摘要与样本数随响应返回。计划摘要对输入中的实际计划字节计算。缺失前序估时会使对应的开始预测不可用，缺失本任务估时会使完成预测不可用。

历史预测只进入 `timing`，`estimated_risk` 留给操作员参考。真实调度过期时，Mission 详情的 Task 返回 `scheduling_deferral.reason` 和 `allows_retry`。工作台根据 Controller 给出的 `window-missed` 显示“窗口已过，自动调度已停止”。

Controller 回归覆盖 120 秒与 180 秒回放、并行汇合、相等边界、缺失估时、新旧响应和提交前拒绝。预检用例同时核对事件游标和应用 checkpoint。原生调度回归核对实际派发次数。
