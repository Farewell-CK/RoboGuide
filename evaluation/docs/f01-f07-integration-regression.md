# F01–F07 集成回归记录

日期：2026-09-18。范围仅为两条修复分支的集成及离线回归。

- main 基线：`e66e8c2e4bb625968a0ae55622dd72da1b58a9b9`。
- F01–F05：`8664074a772406b4ea761533e280aa08635733a5`。
- F06–F07：`ae412ad838f767bb24fbd16ba5bea36df1c1651b`。
- 集成分支：`integration/audit-fixes-f01-f07`。
- 先 fast-forward 到 Core 修复，再 merge evaluation 完整历史；无冲突。

没有运行真实 provider、Habitat Episode 51、Formal B1 或 Pilot。没有改写来源分支、
main、MissionPlan contract、Core 实现或任何真实实验结果。

## 原始反例

| Finding | 实际重跑的测试/路径 | 结果 |
| --- | --- | --- |
| F01 | `ungrounded_unconstrained_pipeline_is_independent_of_registry_presence`：同一 v0.8 plan，registry absent/present，各自完整 Match → Schedule → Proposal → Commit → Bind，并比较绑定结果 | PASS |
| F02 | `distinct_cardinality_defers_without_stopping_other_missions_and_retries`：应用 timer、SQLite checkpoint、Runtime facts；T1 满足后 T2 Ready/未绑定，另一 Mission 完成，增加第二 entity 后 T2 继续 | PASS |
| F03 | 四个 `commit_rejects_*selected_resource*` / `commit_rejects_equivalent_replacement_resource_id`：先建立 Proposal，再撤销/替换 ID、降低容量、改变 kind；普通与 Group Commit 都拒绝；连先验证的另一资源也无 reservation、无 PlanCommitted、无绑定 | PASS |
| F04 | 六个 `registry_watermark_*`：rev1 bind → rev5 admit → checkpoint/restore → rev2 拒绝；同 revision 内容变化拒绝；恢复时 live registry 为 None；无绑定 watermark 不含 routes | PASS |
| F05 | `duration_deadline_arithmetic_is_checked_at_scheduler_boundary`、`duration_timestamp_overflow_and_zero_window_have_explicit_results`、`duration_activation_bounds_are_checked_during_reconstruction`：小于/等于/大于窗口、零、u64 边界 | PASS |
| F06 | `test_benchmark_evidence.py`：官方 bool true/false；missing/malformed/non-bool unavailable；empty/partial/malformed local evidence 不补 true 或零聚合 | PASS |
| F07 | `test_b1_provenance.py`、`test_b1_artifact_chain.py`、`test_b1_failure_boundaries.py`：真实 MI engine/HTTP client、最终 digest、实际 POST receipt、按 Mission/Group 过滤、先 persist admission 再 Harness → metrics → summary | PASS |

A–G truth table 全部通过，包括 provenance invalid 但官方 pddl bool 存在时两个
population 都拒绝；合法 SUT/model failure 保留 formal admission；benchmark unavailable
不会变成 false。Controller 409 的 early-failure 链也通过。

## 集成交互及唯一修复

新回归 `test_b1_scheduling_interaction.py` 在修复前确定失败：即使 Controller 存活且
Mission 仍 Running/Task Ready，scenario 的等待超时仍会向 collector 传入
`SUT_SYSTEM`。这会把 F02 的合法 deferral 误报为系统失败。

只修改 scenario 的观察边界：等待预算耗尽保持失败 owner 未指定；EXIT 仍检查其子进程
是否退出，canonical assessment 仍使用实际 Mission Failed 或明确的边界 failure。
不解析 reason 文本决定调度策略，也不修改 admission 公式。

新增八个离线用例执行脚本中真实的 polling/EXIT 函数（HTTP、计时、进程观察使用 stub），
再把实际 collector 参数送入 provenance → persisted verdict → public Runner → metrics →
summary。覆盖 distinct-entities-unavailable、activation-revalidation、window-missed，
以及之前已有/尚无 attempt 两种情形；真实进程退出或终态失败仍正确归因。

尚无 attempt、也没有 attributable failure 的首个 Ready Task 不伪造完整 provenance；
原有 gate 仍 fail closed，system/infra failure 都为 false。已有有效 attempt 的等待
Mission 保持 formal=true、benchmark=false。这不是通过超时伪造 failure 来补齐 provenance。

另运行了一个真实进程探针：本次构建的 `integration-server`、临时 SQLite、loopback HTTP、
真实 Mission Request engine 和 submission client，使用离线 fake Planner 且不启动 Node。
实际响应为 202；Mission Running、Task Ready、存在 TaskSchedulingDeferred；MI 没有
failure observation，两个 plan digest 相等；collector/Harness 未报告 system/infra failure。
停止并重启本探针的 Controller 后，Mission/Group 和 Ready 状态保留；MI SQLite 也独立恢复。

| 边界 | 确认结果 |
| --- | --- |
| Mission Request observability | public v0.4 字段集合不变；sidecar 保存实际请求 digest；Core acceptance/commit/bind 文件与 Core source 相同 |
| Controller checkpoint | service wrapper v16 内含 integration v15，分别校验版本；registry watermark 不恢复 live routing |
| Mission Request store | `roboguide.mission-request-storage/v0.1` 私有 envelope，原有表内原子保存；legacy bare row 可读，旧 binary 降级仍需显式 migration |
| Evaluation artifacts | provenance v0.3、verdict v0.2、admission v0.1；semantic evidence 绑定和 goal coverage diagnostic 只消费归档，不写入 Controller 或 MI store |
| Scheduling × Eval | AssignmentUnavailable → ActivationRevalidation；distinct shortage → DistinctEntitiesUnavailable；WindowMissed → 非 fatal deferral；没有 failure 事实不产生 SUT/infra failure |

## Gates

- `cargo test --workspace --locked`：470 passed，包含 checkpoint、recovery、stale-attempt、
  command idempotency、Node status reacquisition、v0.7/v0.8 compatibility。
- `cargo fmt --check`：PASS。
- `cargo clippy --workspace --all-targets --all-features --locked -- -D warnings`：PASS。
- Full Python pytest：385 passed，包含完整 Mission Service 和 Habitat adapter 离线测试。
- F06/F07、submission observability、A–G、跨层新回归的定向集合：83 passed。
- Ruff format/check：149 files / PASS。
- 仓库规定的分包 strict mypy：Mission/adapter 60 files；evaluation 43 files，均 PASS。
- Python function-doc check：PASS。
- `bash -n` B1 与 shared-world scripts：PASS。
- `git diff --check`：PASS。

Scheduler 和 Orchestration 的生产 deadline-duration 路径均使用 `checked_sub`；检索未发现
剩余裸减法。Core 与 F01–F05 来源、MI 与 F06–F07 来源的实现保持一致。没有新增证据复现
Gemini backlog 中的真实 blocker；这些 backlog 项没有被扩大审查或修复。
