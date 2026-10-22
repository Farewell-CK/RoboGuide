# 架构与文档站核对记录（2026-10-22）

代码核对基线：`45a82410867e01241394d1b9116aa53634ce2a06`。工作位于独立
`codex/architecture-docs-site`，不消费另一工作树的未提交内容。

## 架构解释与证据

| 核对问题 | 当前解释 | 实际入口 |
| --- | --- | --- |
| 分层与进程 | Controller 共址多个逻辑职责，MI/Node/Local EAIOS 是其他部署参与方 | `apps/integration-server/src/main.rs`、`core/orchestration/src/integration_bridge/mod.rs` |
| Group 创建顺序 | accepted complete plan 创建 Group/所有 TaskExecution，之后逐 Task Commit/Bind | `core/orchestration/src/mission/checkpoint.rs`、`core/control/src/group/mission_binding.rs` |
| 资源与 quota | Control 唯一 reservation authority，原 tenant/Role units 在 Commit/release 原子记账 | `core/control/src/coordination.rs`、`core/control/src/quota.rs` |
| 完成与满足 | Runtime 归约当前 attempt，Orchestration 按 accepted basis 判定 satisfaction | `core/runtime/src/execution/`、`core/orchestration/src/mission/lifecycle.rs` |
| 来源与内容 | State consumer policy、Memory 原 provider use/revoke 与 CAS bytes 分离 | `apps/integration-server/src/application/`、`integrations/memory-provider/` |
| 运行期修订 | 原 goal/claim/nonce/Review 与 stop/revoke 先于新 Group | `core/orchestration/src/mission/revision.rs`、`mission/src/mission/runtime_replanning.py` |
| 恢复范围 | 实际 writer/inode leases 下核对声明数据和 active refs/CAS；不是全局内容覆盖 | `apps/recovery-tool/roboguide_recovery/bundle.py`、ADR-0145 |

[双语手册](../architecture/v2/architecture-handbook.md)解释这些关系。
[10 月 22 日原存量复核](roboguide-branch-audit-2026-10-22.md)提供更完整的实现与缺口矩阵。
既有软件验收不改写成本次重新执行，也不变成设备或当前部署证据。

## 已修正文档问题

旧站首页手写 ADR 数量、Node 语义版本和 checkpoint 版本；模块图还混有早期 NodeGateway
bootstrap 过程和过期取消语义。本轮以当前正式调用链重写模块说明，把版本集中从权威声明
生成；默认、可选 profile、兼容输入与存储 fence 分别解释。

概念图、当前进程拓扑、正常执行时序和异常恢复时序分开。State、Memory、身份、持久化和
独立 verifier 作为横向职责；Actor/PhysicalEntity/Node、Group/Task/attempt 保持区分。

V2、开发基线和原验收是累积演进资料，内部旧版本可能描述原切片的引入状态。它们保留
原来源和历史，新读者先沿手册与状态矩阵阅读。遇到实际语义冲突须带代码/ADR 证据再决策，
不能仅以网页样式修复为由变更协议或 authority。

## 网站维护与发布边界

维护正文全部中英文配对；ADR/审计/验收保留原文，以双语主题和摘要导引。
仅镜像受跟踪的公开材料，排除会议与临时目录；旧页面地址保留，旧标题 anchor 有兼容表。
站点使用本地固定 Mermaid 资源，不依赖外部字体或图示 CDN。

构建记录 source SHA、正文核对 SHA、时间和 dirty 状态；源码链接固定到构建提交。
文档版本变化要求显式复核。严格构建、渲染链接、双语配对、发布边界回归和浏览器检查形成
独立 CI，完整命令见 [website/README](../../website/README.md)。

本轮没有修改运行时代码、Node 合同、checkpoint schema、数据库、模型预算或实验结果。
也没有启动外部模型、Habitat、设备实验或通知他人。
