# 原始文档与验收档案

这里保存架构来源、决策理由和历史证据；新读者先读[架构导览](architecture-tour.md)。维护正文提供完整中英文，历史档案保留原文，切换英文不表示历史全文已经翻译。

| 档案主题 | 内容与边界 | 原文入口 |
| --- | --- | --- |
| V2 架构 | 权威基线、实现拓扑；原 DOCX 是历史快照 | [V2](docs/architecture/v2/index.md) |
| 架构决策 | 编号、原状态与决定；Accepted 不等于全面验收 | [ADR 主题](decisions.md)、[全索引](docs/decisions/index.md) |
| 当前实现核对 | 区分已实现、接线、软件验收与部署缺口 | [最新存量](docs/development/roboguide-branch-audit-2026-10-22.md) |
| Memory / scoped revision | 原授权、导入/use/revoke、非空 Memory 与独立目标验证 | [MI/Memory 组合](docs/development/acceptance/2026-10-21-runtime-mi-scoped-memory.md) |
| 引用与恢复 | active refs 对应原 provider CAS；不是全局覆盖 | [引用恢复验收](docs/development/acceptance/2026-10-21-recovery-active-content-reference.md) |
| 部署与监控 | 零 Node inspector、真实软件监控；不是硬件或长期 SLO | [部署](docs/development/acceptance/2026-10-21-deployment-profile.md)、[监控](docs/development/acceptance/2026-10-20-observability.md) |
| 实验与评估 | 保留样本分母、代码/模型身份、官方判定与不可用案例 | [Eval Harness](evaluation/index.md) |
| 历史架构 | V1.1 与原始设计，不作为当前实现清单 | [V1.1](docs/architecture/v1.1/index.md) |

[仓库文档索引](docs/index.md)提供其余原材料。会议资料、临时文件与本地实验结果不随站点递归发布。
