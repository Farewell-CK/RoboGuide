# 原始文档与验收档案

这里保存架构来源、决策理由和历史证据；新读者先读[架构导览](../architecture/v2/architecture-tour.md)。维护正文提供完整中英文，历史档案保留原文，切换英文不表示历史全文已经翻译。

| 档案主题 | 内容与边界 | 原文入口 |
| --- | --- | --- |
| V2 架构 | 权威基线、五类协议数据流图解、实现拓扑；原 DOCX 是历史快照 | [V2](../architecture/v2/README.md)、[数据流图解](../architecture/v2/system-dataflows.md) |
| 架构决策 | 编号、原状态与决定；Accepted 不等于全面验收 | [ADR 主题](../reference/decision-guide.md)、[全索引](../decisions/README.md) |
| 验收记录全索引 | 143 份 2026-10 / 2026-11 软件与真机验收记录分主题目录（原字保留） | [验收总索引](../development/acceptance/README.md) |
| 历史研发流水账 | 原根目录 `README.md`（`e4c03a55`）10~11 月演进与验收日志无损归档 | [README 归档](../development/readme-development-log-archive-2026-11.md) |
| 当前实现核对 | 区分已实现、接线、软件验收与部署缺口 | [最新存量](../development/roboguide-branch-audit-2026-10-22.md) |
| Memory / scoped revision | 原授权、导入/use/revoke、非空 Memory 与独立目标验证 | [MI/Memory 组合](../development/acceptance/2026-10-21-runtime-mi-scoped-memory.md) |
| 引用与恢复 | active refs 对应原 provider CAS；不是全局覆盖 | [引用恢复验收](../development/acceptance/2026-10-21-recovery-active-content-reference.md) |
| 部署与监控 | 零 Node inspector、真实软件监控；不是硬件或长期 SLO | [部署](../development/acceptance/2026-10-21-deployment-profile.md)、[监控](../development/acceptance/2026-10-20-observability.md) |
| 实验与评估 | 保留样本分母、代码/模型身份、官方判定与不可用案例 | [Eval Harness](../../evaluation/README.md) |
| 历史架构 | V1.1 与原始设计，不作为当前实现清单 | [V1.1](../architecture/v1.1/README.md) |

[仓库文档索引](../README.md)提供其余原材料。会议资料、临时文件与本地实验结果不随站点递归发布。

## 本次更新前的完整原件

[迁移清单](../reference/documentation-inventory.json)覆盖全部原 docs 文件、仓库 README 和 38 份原网站指南，共 571 个原件。未变更历史文件保留原路径；重写或迁移的来源以原 Git blob 字节保存于 `pre-refresh-3c7136d6/`。网站按原文件位置解析其历史链接，避免归档后链接失效。

[Original repository README](pre-refresh-3c7136d6/README.md) · [Original V2 baseline](pre-refresh-3c7136d6/docs/architecture/v2/README.md) · [Original code atlas](pre-refresh-3c7136d6/docs/architecture/v2/current-architecture.md)
