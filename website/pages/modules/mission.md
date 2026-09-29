# 模块地图：Mission Intelligence

对应包：[`mission/`](https://github.com/Farewell-CK/RoboGuide/tree/main/mission)
（Python，`src/mission/` 32 模块约 10,400 行 + 测试约 8,100 行）与组合根
[`apps/mission-service`](https://github.com/Farewell-CK/RoboGuide/tree/main/apps/mission-service)。
模块地图首页 · 上一篇：[Integration 与 Node](integration-node-service.md) · 下一篇：[Eval 与集成](evaluation.md)

**定位**：把用户文本指令转化为版本化 MissionPlan Task Graph 的 deliberation
闭环：解释、阻塞澄清、计划、结构化审查、有界修复、风险审批、提交。它生成
Mission/Task/Context/Role 身份，但**不拥有**节点分配、资源提交、执行组或本地
设备控制，也不镜像执行生命周期。

## 请求流水线（`MissionRequestEngine`）

`create() / add_message() / approve() / retry() / cancel()` 入口，内部
`_process()` 依次：

```text
① Grounding     捕获不可变、digest 绑定的 GroundingContextSnapshot（fail-closed 绑定到请求）
② Interpret     Interpreter 产出 GroundedIntent；阻塞歧义 → NeedsClarification
③ Plan          Planner 生成计划；被拒草案持久化证据后按预算 regenerate（prevalidation recovery）
④ Draft 校验    implementation support / 物理实体 grounding / 语义准入 / Catalog 校验
⑤ Review        Reviewer 结构化问题 → APPROVED / CLARIFICATION / REJECTED / 需修复
⑥ Repair        有界修复（配置 max_repair_attempts），每个修复草案重新过 ④
⑦ Approval      ApprovalPolicy 判定；需要时 AwaitingApproval 等 approve() 精确匹配修订+摘要
⑧ Submit        HttpMissionController.submit_plan → Accepted / Blocked（失败证据持久化）
```

生命周期状态机：`Received → Interpreting → (NeedsClarification ⇄) → Drafted →
Reviewing → (Repairing ⇄) → AwaitingApproval → Submitting → Accepted | Blocked |
Failed | Cancelled`。启动时 `_recover_interrupted()` 把中断态栅栏为 `Failed`
而非盲目续跑。

## 模块分组

| 阶段 | 模块 |
| --- | --- |
| 摄入/接地 | `api.py`（HTTP 组合根）、`grounding_context.py`、`grounding_reader.py`、`semantic_evidence.py`、`planning_world_evidence.py`、`planning_profile.py`、`execution_profile.py` |
| 解释 | `request_record.py`（持久请求投影 + Interpreter 协议）、`intent.py` |
| 计划 | `planners.py`、`responses.py`（Responses-API LLM 适配器 ×4 角色）、`provider_mission_plan.py`、`capability_catalog.py` |
| 审查/修复 | `review.py`（结构化审查 + 路由）、`rejected_draft.py`（被拒草案证据） |
| 准入/审批/提交 | `semantic_admission.py`、`approval.py`、`satisfaction_policy.py`、`controller.py`、`submission_evidence.py` |
| 编排/持久化/配置 | `request_engine.py`、`request_store.py`（SQLite）、`config.py`、`service_config.py`、契约值模块（`models.py` `task.py` `role.py` `context.py`…） |

## Prompts（`mission/prompts/v0/`）

`interpreter.md`（一个自含目标 + 确认约束 + 只问阻塞问题）、`planner.md`
（只用 Catalog 词汇产无环 Task Graph）、`reviewer.md`（独立审查，规划 profile
只当启动冻结摘要）、`repairer.md`（只修结构化问题，保任务身份/目标/同一接地）。

## 关键设计事实

- Interpreter **不读** live Node/Resource inventory——当前无 provider 是 Control
  调度条件，不是语义拒绝（ADR-0030）。
- Grounding Snapshot 只含部署批准 schema 的 World 记录与全局 Memory **元数据**；
  元数据从不代表 payload 已被读取。四个模型角色共享同一 digest 绑定快照（ADR-0037）。
- 计划世界证据（episode/scene/dataset 绑定的静态世界事实）未由 reset 确定时保留
  unknown gap，模型不得推测楼层/起点/可达性（ADR-0044）。
- 满足新鲜度策略显式版本化，Planner/Reviewer/Repairer 消费同一 policy digest，
  不为填 schema 猜数值（ADR-0039）。

## HTTP 面（`apps/mission-service`，默认 `127.0.0.1:8070`）

`GET /healthz`；`POST /v1/mission-requests`（`{"instruction": ...}`）；
`GET .../{id}`、`.../{id}/observations`、`.../{id}/grounding-contexts/{digest}`；
`POST .../{id}/messages`、`.../approve`（`draft_revision`+`draft_digest`）、
`.../retry`、`.../cancel`。配置在 `config/mission.toml`（模型、prompt 版本、
修复预算）与 `config/mission-service.toml`（监听、接地限额、审批规则数组）。

## 测试（23 个文件，全离线）

引擎生命周期、被拒草案恢复、计划器、审查路由、协调指导、接地上下文、契约校验、
能力目录、配置、审批策略、提交可观测性等。

## 实现状态

- MissionPlan 契约：v0.3–v0.5 兼容输入，**v0.8 当前**；Mission Request 投影
  v0.4 当前（v0.1–v0.3 可读）。
- Grounding Context v0.3 仅当存在规划世界证据文件时选用。
- 再生只由 `RejectedPlanError` 触发（传输/鉴权失败不烧预算）。

## 相关 ADR

[0018 意图接地闭环](../docs/decisions/0018-mission-intent-loop.md) ·
[0030 语义接纳与可部署性](../docs/decisions/0030-mission-semantic-admission-and-deployability.md) ·
[0031 规范能力目录](../docs/decisions/0031-canonical-capability-catalog.md) ·
[0032 审查与修复闭环](../docs/decisions/0032-mission-review-and-repair-loop.md) ·
[0034 语义契约规范化](../docs/decisions/0034-mission-semantic-contract-normalization.md) ·
[0037 接地上下文](../docs/decisions/0037-mission-grounding-context.md) ·
[0038 阻塞澄清与接地获取](../docs/decisions/0038-blocking-clarification-and-grounding-acquisition.md) ·
[0039 满足新鲜度策略](../docs/decisions/0039-mission-satisfaction-freshness-policy.md) ·
[0044 部署规划证据](../docs/decisions/0044-deployment-planning-evidence.md)

---

模块地图：[Domain 与端口](domain-ports.md) · [Control](control.md) ·
[Orchestration](orchestration.md) · [Runtime](runtime.md) ·
[State & Memory](state.md) · [Integration 与 Node](integration-node-service.md) ·
[Mission Intelligence](mission.md) · [Eval 与集成](evaluation.md)
