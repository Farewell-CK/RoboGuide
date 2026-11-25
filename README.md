# RoboGuide

面向异构具身智能体协作的分布式具身智能操作系统框架。

RoboGuide 围绕长期 Mission 联合调度 **Capability · Compute · Space · Time**，管理任务、资源承诺、执行、证据与恢复，并保留每个 Local EAIOS 的本地自治与即时安全。

[中文文档](docs/README.md) · [English](docs/README.en.md) · [架构手册](docs/architecture/v2/architecture-handbook.md) · [八主题架构图册](docs/architecture/v2/current-architecture.md) · [双语文档站](https://roboguide-docs.chaokedu.workers.dev)

代码核对：**2026-11-26，`main@3c7136d6`**。文档构建提交与代码核对基线分别记录。

<!-- diagram:overview:zh -->
![RoboGuide 系统总览](docs/images/architecture/2026-11-26/overview.zh.png)

## 一项任务如何经过系统

1. Chat / Mission Service 保存用户意图，取得 Grounding，生成并审查完整计划。
2. Orchestration 接纳 Mission，推进 DAG，并按计划判定任务满足。
3. Control 从合法候选中调度，在实时依据、资源、租户与配额复核后 Commit / Bind。
4. Runtime 持久化 attempt 与 intent；应用事务提交后通过 Node Protocol 派发。
5. Node journal 接纳命令，再运行部署声明的本地 workflow；事实持久接纳后 ACK。
6. 满足性、停止与 Memory 撤权门禁通过后，Control 释放相应所有权并推进任务。

Proposal 不等于 Commit；本地 Completed 不等于目标达成。Unknown 保留原副作用歧义，不授权盲目重放。详见[核心流程](docs/architecture/v2/system-dataflows.md)。

## 从哪里开始

| 目标 | 入口 |
| --- | --- |
| 运行第一条软件闭环 | [快速开始](docs/getting-started/quickstart.md) |
| 理解对象与架构 | [V2 语义](docs/architecture/v2/README.md) · [概念](docs/architecture/v2/concepts.md) |
| 修改 Rust / Python 核心 | [模块地图](docs/guides/development/modules/domain-ports.md) · [开发规范](docs/guides/development/coding-standards.md) |
| 接入机器人或 Provider | [开发与接入](docs/guides/integration/README.md) |
| 部署、诊断、备份与恢复 | [运维](docs/operations/README.md) |
| 查明版本和验证边界 | [版本](docs/reference/contracts-and-build.md) · [实现状态](docs/reference/implementation-status.md) |
| 查证设计与历史结果 | [ADR](docs/reference/decision-guide.md) · [原始档案](docs/archive/README.md) |

## 仓库组成

| 目录 | 职责 |
| --- | --- |
| `core/` | Domain、Ports、State、Control、Orchestration、Runtime、Integration、Node Service、Artifact 与可选复制 |
| `apps/` | Controller、Node、Mission Service、Replica、Recovery Tool 与协议探针组合根 |
| `mission/` | Mission Intelligence、运行期重规划与 `roboguide chat` |
| `integrations/` | Robonix、Memory、Spatial、Sync、Habitat 与 COHERENT 部署适配器 |
| `contracts/` | 分别版本化的跨语言语义合同与 wire 协议 |
| `config/` / `scenarios/` | 无凭据模板、场景与验证配置 |
| `evaluation/` / `tools/` | 评测、部署检查、观测和质量门禁 |
| `console/` | 任务旅程控制台 |
| `docs/` / `website/` | 唯一正文来源与双语静态站点构建 |

## 最小软件示例

Rust/Python 版本由仓库固定。包含 Unix 存储/进程路径的服务在 Linux 或 WSL 环境运行。

```bash
# 内置 FakeNode 与虚拟时钟的软件切片，不连接实际设备
cargo run --locked -p controller

# 仅校验本地 Node 配置
cargo run --locked -p roboguide-node -- --validate scenarios/extension-conformance-v0.1/node.toml
```

完整的握手、MI、Chat、Console 与文档站步骤见[快速开始](docs/getting-started/quickstart.md)。

## 当前能力与证据

当前主线包含多机异构计划执行、来源 State、Memory/CAS、空间融合、协作关系、运行期修订与显式恢复。PR #42 增加部署侧同步行进与定时编排 Provider。能力实现、软件验证、设备效果、跨主机连续性和长期 SLO 必须分别核对；具体范围见[实现状态](docs/reference/implementation-status.md)。

现行图须经生图 API 生成并按源码核对后才能发布，提示词和实际生成状态见[图片来源](docs/images/architecture/2026-11-26/README.md)。历史 V2 PNG、原始 DOCX、ADR 与验收记录保留原时点，见[档案](docs/archive/README.md)。
