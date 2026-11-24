# 架构导览

先区分三种视图：逻辑架构解释谁负责，代码模块说明实现位置，部署拓扑说明哪些进程实际运行。
[V2 现行架构](docs/architecture/v2/index.md)已按当前主线整合；[架构手册](docs/architecture/v2/architecture-handbook.md)提供阅读路径。

## 系统定位

RoboGuide 联合协调 Capability、Compute、Space、Time，以 Mission 为长期目标组织异构系统。全局协调处理目标、资源和协作约束，Local EAIOS 保留 Local How 与最终安全。

## 分层与权威

| 层 | 决定什么 | 深入阅读 |
| --- | --- | --- |
| MI | 解释、澄清、规划、Review、审批 | [Mission Intelligence](modules/mission.md) |
| Orchestration | 接纳完整计划、DAG、satisfaction、Mission completion | [编排](modules/orchestration.md) |
| Control | eligibility、资源承诺、绑定、Group、恢复决定 | [Control](modules/control.md) |
| Runtime | 当前 attempt、durable intent、事实归约、relation fence | [Runtime](modules/runtime.md) |
| Integration / Node | 正式传输、节点 journal、本地工作流接入 | [节点与协议](modules/integration-node-service.md) |

State、Memory、身份和持久化横跨这些职责；它们不是一条串行流水线的最后一步。

## 当前代码架构

按 2026-10-22 主线代码核对。Controller 内部职责共用应用事务；State、Memory 与验证横向支撑执行。

<!-- architecture-overview:start -->
```mermaid
flowchart TB
  %% roboguide:min-width: 1280px
  U[用户 / Operator] --> MI[Mission Service · Python<br/>Request / Grounding / Plan / Review / 审批]
  MW[可选运行期 MI worker<br/>原 Request / claim / provider nonce] -->|候选修订 + 独立 Review| O
  MI -->|完整 MissionPlan · 应用接纳| O

  subgraph CTRL[Controller 进程]
    O[Orchestration<br/>Task DAG / 满足判定 / Mission 生命周期]
    C[Control<br/>Match / Scheduler / Commit / Bind<br/>Group / quantitative Compute / 时空日历 / tenant quota]
    R[Runtime<br/>slot / attempt generation<br/>durable intent / 事实归约 / relation fence]
    I[Integration<br/>Node Protocol / session / 路由]
    O -->|Ready Task| C
    C -->|已提交绑定| R
    R -->|命令 intent| I
    R -->|执行结果| O
    S[State + Memory catalog<br/>来源 / 时间 / TTL / 策略 / manifest 元数据]
    M[应用层 Memory 协调<br/>typed scope / Prepare / import / use / revoke / handoff]
    A[Artifact adapter + CAS<br/>独立内容授权 / 哈希字节]
    I -->|经应用接纳的来源事实| S
    S -.当前依据.-> C
    S -.目录 / selector.-> M
    M -->|执行前与释放门禁| O
    M -->|持久 Memory 命令| I
  end

  I <-->|gRPC · 命令 / durable ACK / 事实| N[Node Service · Rust<br/>配置编译 / Local Integration Engine / journal]
  N <-->|HTTP / gRPC / MCP 工作流| L[部署侧 Local EAIOS<br/>实际操作 / Local How / 即时安全]
  N <-->|配置声明的 Memory 工作流| P[Local Memory provider<br/>原内容 / 当前使用许可 / Active Map / GC]
  N <-->|选择性交换内容 bytes| A
  V[部署侧独立 verifier / 原目标证据源] -->|原 plan / predicate / attempt / freshness| O
```
<!-- architecture-overview:end -->

[完整 Mermaid 架构图册](docs/architecture/v2/current-architecture.md)包含执行与持久化顺序、进程/存储部署、
Habitat/Eval 接入图、代码依据表和可下载源码。零 Node 有效；Node 不等于 Host。
Replica 元数据共识、内容恢复和物理连续性分别有自己的合同与证据。旧 V2 PNG 保留为历史概念快照。

## 不变量

- Proposal 不产生 reservation，Commit 必须重验当前依据。
- Group 由 Control 管理，运行上下文由 Runtime 承载；Task 释放不等于销毁 Group。
- 本地 Completed 不自动成为 TaskSatisfied；Unknown 保留歧义与 ownership。
- State 保留来源、receive time 与 TTL，不隐式覆盖独立来源。
- Memory Scope、Visibility、Placement 独立；CAS 引用不证明接收端已导入。
- 恢复保留原事实、预算与权限；设备安全仍由 Local EAIOS 及独立证据约束。

## 三平面通信与四种协作耦合模式

系统运行期将 **控制面（HTTP :8080 / gRPC :50051）**、**状态与空间记忆 CAS 面（:8090 / Storage v3）** 与 **Mode 4 端到端直连数据面** 分层解耦，五类细化协议数据流见[核心系统数据流图解](docs/architecture/v2/system-dataflows.md)（含部署拓扑、任务控制流、遥测瘦身与空间融合流、协作分流与分级恢复流）。

```mermaid
flowchart TB
  CHAT["roboguide chat / Mission Console :8095"] -->|"HTTP"| MS["Mission Service :8070"]
  MS -->|"POST /validate-plan, /v1/missions"| CTRL["Controller (:8080 HTTP / :50051 gRPC)"]
  CTRL ---|"SQLite Storage v3 + ADR-0257/0258 瘦身"| CAS["Artifact HTTP & CAS :8090"]
  SP["Spatial Provider (多机地图融合)"] -->|"SHA-256 不可变包"| CAS
  CTRL <-->|"gRPC :50051 Execute / Snapshot / PeerReadiness"| NODE["roboguide-node + Local EAIOS"]
  NODE <-->|"mTLS 上传 / 下载地图字节 :8090"| CAS
  NODE <-->|"Mode 4 边缘直连数据面 (零经由 Controller)"| PEER["同伴 Local EAIOS"]
```

| 协作模式（ADR-0026 / ADR-0027） | 控制面协调机制 | 数据面与本地安全归属 |
| --- | --- | --- |
| `Independent` | 标准 Task DAG、`Match -> Commit -> Bind` | 各节点独立执行 |
| `SequentialHandoff` | 前序 `TaskSatisfied` + Memory/Artifact 释放门禁 | 经 `:8090` CAS 与显式 `import / use` 交接 |
| `ConcurrentCooperation` | `RequiresActive` / `SharedSpatialReference`、`GET /v1/missions/{id}/shared-view`、`relation_fences` 与 `/reconcile` | 共享状态按 receive-time TTL 分类；本地避障由 Local EAIOS 自持 |
| `TightlyCoupledCooperation` | Runtime 仅管理双端 `PeerChannel`（`Planned -> Ready -> Fenced -> Closed`） | 高频相对控制与断链齐刹直连边缘节点，Controller 零经过数据面流量 |

## 下一步

[核心概念](concepts.md)解释对象关系；[执行生命周期](lifecycle.md)串起正常与异常路径；[State 与 Memory](state-memory.md)说明数据和证据；[实现状态](status.md)列出实际边界。
