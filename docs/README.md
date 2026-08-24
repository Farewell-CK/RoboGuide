# 文档索引

## 当前架构

- [`architecture/v2/RoboGuide_Architecture_Baseline_V2.docx`](architecture/v2/RoboGuide_Architecture_Baseline_V2.docx)：当前架构权威源文档，包含 V2 总体架构图；
- [`architecture/v2/README.md`](architecture/v2/README.md)：V2 仓库内结构化摘要；
- [`images/roboguide-v2-overall-architecture.png`](images/roboguide-v2-overall-architecture.png)：V2 总体架构独立原图。

## 项目范围

- [`project-goals-and-mvp.md`](project-goals-and-mvp.md)：通用 OS 目标、多机异构 MVP 和应用演进；
- [`mvp-definition.md`](mvp-definition.md)：完整 MVP 草案、已批准的 Slice v0.1、候选场景和冻结清单；
- [`implementation-backlog.md`](implementation-backlog.md)：V2 开放架构问题、待定 MVP 决策和延后实现项。

## 开发

- [`development/README.md`](development/README.md)：`Proposed` 开发基线、目标目录、模块职责、依赖方向和变更门槛；
- [`development/coding-standards.md`](development/coding-standards.md)：Rust/Python 函数文档、类型、错误处理、测试和质量门槛；
- [`decisions/0001-rust-core-python-edges.md`](decisions/0001-rust-core-python-edges.md)：`Proposed` Rust 核心与 Python 边缘职责 ADR。
- [`decisions/0002-deaios-node-contract.md`](decisions/0002-deaios-node-contract.md)：DEAIOS 与本地 EAIOS/厂商运行时之间的 Node Contract v0。
- [`decisions/0003-mission-plan-contract.md`](decisions/0003-mission-plan-contract.md)：Mission Intelligence 向 Rust 核心交付 Task Graph 的 MissionPlan v0 合同。
- [`decisions/0004-recovery-commitment-lifecycle.md`](decisions/0004-recovery-commitment-lifecycle.md)：Recovery Commit 到 Rebind 之间的 Pending、Consume、Abort 和 terminal cleanup ownership。

## 历史架构

- [`architecture/v1.1/README.md`](architecture/v1.1/README.md)：V1.1 历史摘要；
- [`architecture/v1.1/Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx`](architecture/v1.1/Distributed_Embodied_AI_OS_总体架构详细设计说明书_V1.1.docx)：V1.1 原始文档；
- [`images/distributed-embodied-ai-os-architecture-v1.1.png`](images/distributed-embodied-ai-os-architecture-v1.1.png)：V1.1 历史架构图。

新的外部架构资料应先完整阅读，再归入 `architecture/<version>/`。不要把 DOCX、PDF
或临时图片长期留在仓库根目录。
