# 当前实现与验证边界

代码核对：2026-11-26 · `main@3c7136d6`（PR #42）。本页描述仓库中的软件实现；历史验收、单元测试与真机观测分别构成不同范围的证明。

## 当前能力地图

| 能力 | 责任与代码入口 | 仍需独立验证的范围 |
| --- | --- | --- |
| 对话到计划 | [Request 引擎](../../mission/src/mission/request_engine.py)、[Chat 工具](../../mission/src/roboguide_workbench/task_tools.py)：Grounding、澄清、Review、审批及有歧义的提交对账 | 实际模型、工具来源与操作员场景 |
| 接纳与联合调度 | [Orchestration](../../core/orchestration/src/lib.rs) 管 DAG/满足性；[Control](../../core/control/src/lib.rs) 管准入、Commit、Bind、资源与 Group | 部署声明的候选、拓扑、租约和配额 |
| 可靠执行 | [Runtime](../../core/runtime/src/lib.rs)、[Node Service](../../core/node-service/src/service/mod.rs)：attempt、durable intent、journal、幂等与 ACK | 真实工作流的物理效果、停止与时延 |
| 完成与停止 | [满足判定](../../core/orchestration/src/mission/lifecycle.rs)、[安全停机](../../apps/integration-server/src/application/safety_stop.rs)：Completed、TaskSatisfied、stop confirmation 与释放分开 | 独立目标证据与当前 SafeState 来源 |
| State / Memory | [来源 State](../../core/state/src/state_record.rs)、[Memory preparation](../../apps/integration-server/src/application/memory_preparation.rs)、[空间 Provider](../../integrations/spatial-provider/README.md)：时效、作用域、可见性、放置与使用许可 | 标定、地图配准、定位精度与隐私授权 |
| 恢复与修订 | [恢复协调](../../apps/integration-server/src/application/recovery.rs)、[计划切换](../../apps/integration-server/src/application/plan_revision/switching.rs)、[恢复工具](../../apps/recovery-tool/README.md) | 原 stop/revoke 与策略门禁；Unknown 不授权重放 |
| 四种协作模式 | [关系 Runtime](../../core/runtime/src/relation/manager.rs)、[Sync Provider](../../integrations/sync-provider/README.md)：独立、屏障、共享 Context、边缘耦合 | 局部闭环、断链停止及实际多设备精度 |
| 部署与副本 | [Controller](../../apps/integration-server/src/main.rs)、[Replica](../../apps/controller-replica/src/main.rs)、[部署合同](../operations/reference/replicated-federation-deployment-v0.1.md) | TLS/成员、进程所有权、多数派、内容可用性与长期 SLO 分别核对 |

## 最近主线变化

- **PR #42**：EAIOS-neutral 的 `mobility.sync-follow@v1` 与 `choreography.play@v1`，提供 sim、HTTP 底盘与 Robonix Lite3 适配。测试覆盖模拟与 Controller/Node loopback；不能推断当前设备已通过同步精度或停止验收。
- **PR #40**：共享视图与关系 reconcile 路由，以及终态 attempt 的受控人工取消结案；原关系、当前身份与停止证据仍是门禁。
- **PR #32/#33**：独立 SafeState 和运行期动态接管；自然执行失败不自动证明副作用停止。
- **PR #30**：只读依赖时间窗前检，不承诺资源或执行成功。
- **PR #25/#18**：多机占据栅格融合与接收方/会话绑定，不等于物理导航安全。

机制见[八主题图册](../architecture/v2/current-architecture.md)、[核心流程](../architecture/v2/system-dataflows.md)与[源码版本参考](contracts-and-build.md)。

## 如何判断证明

| 证明 | 可以支持 | 不能独立支持 |
| --- | --- | --- |
| 本次源码核对 | 当前职责、调用路径、协议和门禁存在 | 当前部署已启用或现场有效 |
| 单元与离线测试 | 覆盖输入下的逻辑和文件约束 | 真实跨主机故障或设备效果 |
| 进程 / loopback 验收 | 原记录声明的软件进程闭环 | 硬件停止、定位精度或长期 SLO |
| 真机验收 | 原设备、配置、环境与时点的观测结果 | 所有设备、部署和后续版本 |
| 长期运行记录 | 明确窗口、负载与故障域内的指标 | 未测负载或未声明的安全保证 |

本次文档刷新不运行机器人工作流，不新增真机、生产或长期性能结论。

## 原始记录

[验收目录](../development/acceptance/README.md) · [热注册验收](../development/hot-node-enrollment-acceptance-20261109.md) · [地图副本身份](../development/acceptance/2026-11-17-map-replica-identity.md) · [人工取消结案](../development/acceptance/2026-11-17-failed-manual-cancel-settlement.md)

[刷新前完整状态页](../archive/pre-refresh-3c7136d6/website/pages/status.md)保留其原始叙述和日期；[迁移清单](documentation-inventory.json)与[档案入口](../archive/README.md)保存历史追溯路径。
