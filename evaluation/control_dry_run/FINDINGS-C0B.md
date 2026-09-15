# C0-B MissionPlan → Control Boundary Replay FINDINGS

- Baseline: `main@e6030e3`（C0 Control Dry-run 提交之后）
- Method: **源码级 path tracing + 既有 Core 测试覆盖映射 + fixture 结构符合性核验**。
  与 C0 结论一致：`ControlPlane` pipeline 依赖 `pub(crate)` 内部组件，无法从
  `core/` crate 外部（包括 evaluation/）驱动 integration test，因此 C0-B 不新增
  任何 Rust test，不改任何 Core 代码，不修改 YAML gold。
- 结果: 生产链路完整存在且逐字段保持语义；`-p control` 92/92、`-p orchestration`
  52/52 通过；3 个 v0.7 fixture 结构符合性核验全部通过。

## 1. 真实生产调用链（非 legacy fixture 路径）

之前 C0 记录的 `apps/controller/src/main.rs::run_mvp_slice()` 走的是
`scenarios/mvp-slice-v0.1/mission-plan.json` 的 **schema v0.1 legacy fixture**。
v0.7 的真实生产入口在 integration-server 的 Controller HTTP：

| 步骤 | 位置 | 行为 |
| --- | --- | --- |
| 1. HTTP 入口 | `apps/integration-server/src/controller_http/server.rs:147` | `POST /v1/missions` → `decode_mission_plan(request_body)`，decode 失败返回 400 |
| 2. v0.7 decode | `core/orchestration/src/mission_contract/decode.rs` | timing 必填、拒绝 Planner `estimated_duration_ms`、operation 必填 fail-closed、语义 intent 用 `ExecutionIntent::new_semantic` |
| 3. 原子接受 | `server.rs:161-214` | 在**克隆的候选 controller** 上执行 `validate_actor_placement_coverage` → `orchestrator.submit` → `bridge.register_execution_relations` → `drive_ready_tasks` → `server_checkpoint_json`；成功才 `commit_batch` 并把候选换入 live controller，任一步失败 `rollback_batch` 返回 409/503，live 权威不受影响 |
| 4. Mission 接受 | `core/orchestration/src/mission/lifecycle.rs`（`MissionOrchestrator::submit`） | Mission-level Group 创建、完整 DAG 驱动 |
| 5. 就绪调度 | `core/orchestration/src/mission/scheduling.rs:150`（`prepare_task_with_optional_duration_estimate`） | requirement **直接从已接受 MissionPlan 的 task graph 克隆**（scheduling.rs:165-176），不做第二次转换 |
| 6. Matching | `core/control/src/matching.rs:166`（`match_capabilities_for_mission`） | mission_id 交叉校验、task 必须在已接受 plan 内、operation 从 MissionPlan 侧提取、actor 需求首用收窄 |
| 7. Scheduling | `scheduling.rs:295-303` | `schedule_task_with_snapshot_and_estimate`（无决策时）；已有 future 保留则按窗口复用/失效 |
| 8. Propose→Commit→Bind | `scheduling.rs:429-491` | `propose` → `commit_for_group_with_state` → `bind_task_execution_with_requirement`；scheduled 情况下失败会 `invalidate_scheduled_task` 并 deferral，不污染日历 |

重试幂等性有专门测试：`exact_mission_submission_retry_is_idempotent`。

## 2. 字段级 preservation audit

| v0.7 字段 | decode 后载体 | Control 消费点 | 保持证据 |
| --- | --- | --- | --- |
| `mission.id` | `MissionGoal.mission_id` → `TaskRef` | matching.rs:175 交叉校验 | 跨 mission 的 requirement 被显式拒绝 |
| `task.id` | `TaskRef.task_id` | scheduling.rs:170 | 不在已接受 plan 内 → "Task is absent from the accepted MissionPlan" |
| `roles[].id` | `RoleRequirement.role_id` | matching.rs:266-280 逐 role | `NoCandidate` 按具体 role 报错 |
| `roles[].actor`（经 context_role） | `RoleRequirement.actor_id` | matching.rs:227-253 | actor 已绑定则跳过；未绑定才用 Mission 声明需求收窄（不创建预留） |
| `requirements.capabilities[]` | `CapabilityRequirement::exact(contract)` + constraints | node_state.rs:688 `capability_requirement_is_available` | exact contract + 属性约束逐条谓词，非能力名模糊匹配 |
| `requirements.resources[]` | `ResourceRequirement{kind, units}` | matching + scheduler | decode 拒绝重复 resource kind；`units` 是最小容量 |
| `execution_intent.operation` | MissionPlan 侧 `task.execution_intent(role_id)` 提取为 `role_operations` | matching.rs:190-223 exact-operation 门 | 缺 intent → "MissionPlan lacks ExecutionIntent for role"；无 operation 支持的节点被 retain 过滤 |
| `execution_intent.objective/parameters` | `ExecutionIntent::new_semantic` | **不进** Matching/Scheduler 谓词 | Matching 不解释 objective/参数（语义完整性由 Node 侧 workflow 映射保留） |
| `timing.earliest/latest/deadline` | `TaskRequirement::new_scheduled` | `schedule_task_with_snapshot_and_estimate` | t2 fixture `3600000ms` 双侧窗口结构核验通过；v0.7 拒绝 `estimated_duration_ms` 混入 |
| `timing.estimated_duration_ms` | **v0.7 decode 拒绝** | duration 证据走独立来源 `prepare_task_with_duration_estimate` | decode.rs:305-310 fail-closed |
| `depends_on` | `TaskGraph` | `ready_tasks` DAG 就绪 | m1 fixture 2 任务链（relocate→verify） |
| `satisfaction` | MissionPlan 侧 `AwaitingSatisfaction` | **不进** Control | `verifier_evidence_is_distinct_from_local_execution_completion` |

## 3. 既有测试覆盖映射（C0-B 边界）

- **decode 边界**：`decode_mission_plan`、`legacy_plan_schema_is_rejected`、
  `mission_acceptance_rejects_unrepresentable_absolute_timing`、
  `requirement_from_document`、`normalized_role_from_document`、`task_from_document`
- **submit/接受边界**：`exact_mission_submission_retry_is_idempotent`、
  `unsupported_relation_never_reaches_control_authority`、
  `execution_relation_rejects_unknown_or_dag_ordered_endpoints`
- **调度/dispatch 边界**：`future_scheduling_reservation_runs_through_control_lifecycle`、
  `restored_orchestration_cross_checks_control_authority`
- **语义边界**：`verifier_evidence_is_distinct_from_local_execution_completion`、
  `distributed_spatial_memory_actor_placement_drives_two_node_assignments`
- Control 内部 Match/Propose/Commit/Bind/Scheduler 边界由 `-p control` 92 个测试覆盖
  （映射见同目录 `FINDINGS.md` C0-1..C0-7）

## 4. Fixture 结构符合性核验（evaluation 侧，静态）

对 `evaluation/control_dry_run/fixtures/*.json` 按 decode.rs 的 v0.7 门限规则做
Python 静态核验（不执行 Rust decode，仅结构断言）：

- `m1-relocate-then-verify-plan-v0.7.json`: PASS（2 tasks, 2 roles, DAG 链）
- `n4-edge-inference-plan-v0.7.json`: PASS（1 task, 1 role）
- `t2-start-window-plan-v0.7.json`: PASS（1 task, 1 role，双侧 3600000ms 窗口）

## 5. 结论

| 问题 | 答案 |
| --- | --- |
| MissionPlan v0.7 → Control 生产链路存在？ | ✅ `POST /v1/missions` → decode → 原子 submit → prepare_task 全链（§1） |
| 接受路径是否原子？ | ✅ 候选 controller 克隆 + 事件批量 + checkpoint，成功才换入 live；失败回滚（§1 步骤 3） |
| 字段语义在边界保持？ | ✅ 逐字段审计无丢失/无静默改写；objective/parameters 与 satisfaction 按 V2 语义不进 Control（§2） |
| execution 双臂边界？ | ✅ runtime 成功 ≠ 语义满足，`AwaitingSatisfaction` 证据链由专门测试覆盖 |
| implementation gap / architecture gap | 无。唯一限制仍是 C0 已记录的：ControlPlane 是 Rust 内部组件，evaluation 侧只能通过 HTTP 或测试映射间接驱动 |

## Core blocker list

**无**。本轮未发现需要 Codex 修改的项。

## 约束遵守

- 未修改任何 Core / apps / contracts / mission 生产代码；误写入 `core/control/tests/`
  的测试文件已删除，`core/` 工作树干净。
- 未修改 YAML gold、未运行真实模型。
- 本轮新增仅：本文件与 `evaluation/control_dry_run/fixtures/` 下 3 个 v0.7 fixture。
