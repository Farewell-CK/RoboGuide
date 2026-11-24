# RoboGuide 核心系统架构与五类核心数据流图解

> **System Architecture & Five Core Protocol Dataflows (Baseline: `origin/main@e4c03a55`)**
>
> 本文档依据 `e4c03a55` 主线真实源码整理，与 [V2 现行架构](README.md)、[当前代码架构图册](current-architecture.md) 保持同一语义权威。所有端口、HTTP 路由、gRPC 消息体与持久化表名均来自仓库实际实现，不引入虚构接口或隐式权威。

## 0. 默认端口、协议与代码边界总览

| 通信面 / 组件 | 默认端口与协议 | 核心职责与源码锚点 |
| --- | --- | --- |
| **Mission Service** | `127.0.0.1:8070` HTTP（现场可配置如 `:8073`） | 自然语言 Request、Grounding、`Plan / Review / Repair` 与审批提交；[`config/mission-service.toml`](../../../config/mission-service.toml) · [`mission/src/mission/`](../../../mission/src/mission/) |
| **Controller HTTP** | `127.0.0.1:8080` HTTP / mTLS | Mission 准入前检、调度与状态联邦、共享视图、安全停机核验与恢复控制；[`apps/integration-server/src/controller_http/server.rs`](../../../apps/integration-server/src/controller_http/server.rs) |
| **Artifact HTTP & CAS** | `127.0.0.1:8090` HTTP / mTLS | 不可变字节上传与 SHA-256 校验（CAS）、`/v1/maps` 与 `/v1/memories` 目录与副本凭证；[`apps/integration-server/src/artifact_http/server.rs`](../../../apps/integration-server/src/artifact_http/server.rs) |
| **Node Protocol v0.4** | `0.0.0.0:50051` gRPC 双向流（支持 mTLS） | 节点注册、心跳租约、`Execute / Cancel`、`ExecutionSnapshot`、`StateObservationBatch`、`PeerChannelReadiness` 与 `Ack`；[`contracts/node/v0.17/roboguide-node.proto`](../../../contracts/node/v0.17/roboguide-node.proto) |
| **Mission Console** | `127.0.0.1:8095` HTTP 同源只读代理 | 静态控制台与只读代理，仅转发 `/v1/events`、`/v1/inventory`、`/v1/missions` 等已有路由；[`console/serve.py`](../../../console/serve.py) |
| **Local EAIOS / Provider** | 本地 HTTP / gRPC / MCP / Unix Socket | 承载本地导航、底盘即时安全、点云/栅格建图与融合，完全独立于 RoboGuide Core；[`integrations/`](../../../integrations/) |

---

## 图 1：跨进程 / 跨主机部署与通信拓扑图

展示用户端（`roboguide chat`、只读 `Mission Console`、外部/移动端操作端）、`Mission Service`、中心 `Controller`（`integration-server` 与可选 `controller-replica`）以及边缘节点（`roboguide-node` + Local EAIOS + Spatial/Memory Provider）之间的进程与网络边界。

```mermaid
flowchart TB
  subgraph USERS["客户端与运维接入层"]
    CHAT["roboguide chat (roboguide_workbench)"]
    CONS["Mission Console :8095 (静态前端 + 只读代理)"]
    OP["外部 Operator / Mobile 客户端"]
  end

  subgraph MI_HOST["规划服务进程 (Python)"]
    MS["Mission Service :8070 (可配如 :8073)"]
    MW["可选运行期 MI Worker (mission-runtime-replanner)"]
  end

  subgraph CTRL_HOST["Controller 控制域 (Rust · integration-server)"]
    CHTTP["Control & Operator HTTP :8080 (TLS / mTLS 可选)"]
    GRPC["Node Protocol v0.4 gRPC :50051"]
    AHTTP["Artifact HTTP & CAS :8090"]
    DB["SQLite Event Log (Storage v3) + Checkpoint v60 + CAS"]
    REP["可选 controller-replica (OpenRaft 三成员共识)"]
    CHTTP --- DB
    GRPC --- DB
    AHTTP --- DB
    DB -.->|"可选元数据复制"| REP
  end

  subgraph EDGE_A["边缘主机 / 节点 A"]
    NA["roboguide-node A (Local Integration Engine + WAL)"]
    LA["Local EAIOS A (导航 / 运控 / 即时安全)"]
    MPA["Local Memory Provider A"]
    NA <-->|"本地 HTTP / gRPC / MCP"| LA
    NA <-->|"本地 Memory Workflow"| MPA
  end

  subgraph EDGE_B["边缘主机 / 节点 B & 计算服务"]
    NB["roboguide-node B (Local Integration Engine + WAL)"]
    LB["Local EAIOS B (导航 / 运控 / 即时安全)"]
    SP["Spatial Provider (roboguide_spatial_provider)"]
    NB <-->|"本地 HTTP / gRPC / MCP"| LB
    NB <-->|"本地 Unix Socket / HTTP"| SP
  end

  CHAT -->|"POST /v1/mission-requests"| MS
  CHAT -.->|"GET /v1/state/records, /safety-stop"| CHTTP
  CONS -.->|"GET /v1/events, /inventory, /missions"| CHTTP
  CONS -.->|"只读代理转发"| MS
  OP -->|"mTLS Operator / Verifier API"| CHTTP
  MS -->|"POST /v1/missions/validate-plan, /v1/missions"| CHTTP
  MS -.->|"GET /v1/state/records, /v1/maps"| AHTTP
  MW <-->|"GET /v1/runtime-plan-work, POST /plan-revisions"| CHTTP

  NA <-->|"gRPC :50051 双向流 (mTLS 可选)"| GRPC
  NB <-->|"gRPC :50051 双向流 (mTLS 可选)"| GRPC
  NA <-->|"mTLS 上传 / 下载不可变 CAS 字节 :8090"| AHTTP
  NB <-->|"mTLS 上传 / 下载不可变 CAS 字节 :8090"| AHTTP
  LA <-->|"Mode 4 端到端直连数据面 (零经由 Controller)"| LB
```

---

## 图 2：任务全生命周期控制流（Mission & Execution Data Flow）

从自然语言意图输入、Grounding 与规划审查，到只读前检（`validate-plan` + `?timing=v0.1` + Node 私有前检）、Control 原子承诺、SQLite 持久化、gRPC 下发执行、独立满足证据核对与资源释放的完整闭环。

```mermaid
flowchart TB
  REQ["用户输入 / POST /v1/mission-requests"] --> GRD["Grounding Snapshot (只读 State + Global Memory 元数据)"]
  GRD --> DELIB["Interpreter -> Planner -> Reviewer -> Bounded Repair (Mission Service)"]
  DELIB -->|"NeedsClarification"| REQ
  DELIB -->|"MissionPlan 草案"| PRE1["只读前检: POST /v1/missions/validate-plan"]
  PRE1 --> PRE2["可选参数 ?timing=v0.1 / history-v0.1 (ADR-0259 依赖时间窗诊断)"]
  PRE2 --> PRE3["可选 Node 私有前检 ROBOGUIDE_NODE_PREFLIGHT_CONFIGS (ADR-0255/0256)"]
  PRE3 -->|"草案冲突返回有界修复"| DELIB
  PRE3 -->|"通过审批"| ADMIT["POST /v1/missions 正式准入"]

  ADMIT --> ORCH["Orchestration 创建 Group 与 TaskExecutions (DAG Ready)"]
  ORCH --> CTRL["Control: Match -> BoundedJointScheduler -> Propose -> Coordinate -> Commit -> Bind"]
  CTRL --> PERSIST["原子事务持久化: SQLite Event Log + Controller Checkpoint v60 + Durable Outbox"]
  PERSIST -->|"gRPC :50051 Execute (含 command_id, execution_id, resources)"| NODE["roboguide-node: SQLite WAL 落盘 -> Local EAIOS 执行"]

  NODE -->|"gRPC CommandReceipt / ExecutionEvent / ExecutionSnapshot"| INGRESS["IntegrationRuntimeBridge 事实归约 (先持久化后 Ack)"]
  INGRESS -->|"本地 Completed"| AWAIT["Task 进入 AwaitingSatisfaction (保留绑定与资源)"]
  AWAIT --> VERIFY["按声明 basis 核验: execution-report 或 POST /v1/verifier-evidence"]
  VERIFY -->|"正证据 + Memory revoke 完成"| SAT["TaskSatisfied -> 释放 Task 资源 -> 推进后继 DAG"]
  VERIFY -->|"负最终证据"| REJ["TaskSatisfactionRejected -> 按策略隔离或失败"]
  SAT -->|"全部 Task 满足 + 可选 /v1/mission-goal-evidence"| DONE["Mission Completed -> Whole-Group Release"]
```

---

## 图 3：高频遥测事件瘦身与空间记忆 CAS 数据流

系统将高频遥测状态与大体积空间地图字节彻底隔离在两条不同管线上：左/上分支通过节点合并、进程内租约续期与变更/间隔落盘抑制 SQLite WAL 膨胀；右/下分支通过 `:8090` CAS 内容寻址、多机占用栅格融合与受信拉取保证地图字节一致性。

```mermaid
flowchart TB
  subgraph STREAM_A["数据流 A: 高频遥测事件瘦身与投影分流 (PR #19/#20/#21)"]
    E1["Local EAIOS / Host 周期采样 (姿态 / 电量 / 相机 / 状态)"] --> OUTBOX["roboguide-node CoalescedStateOutbox (按 export_id 合并防拥塞堆积)"]
    OUTBOX -->|"gRPC Heartbeat / StateObservationBatch (最大 64 条 / 512 KiB)"| BATCH["Controller fact_batch 准入"]
    BATCH --> LIVE["更新内存态实时投影 (GET /v1/state/records 始终可读最新样本)"]
    BATCH -->|"ADR-0257 进程内 Liveness 续期"| LIVENESS{"是否首次会话或健康状态跳变?"}
    LIVENESS -->|"是"| SQL_EVENT["写入 Storage v3 controller_v3_events + Checkpoint"]
    LIVENESS -->|"否 (稳态心跳)"| MEM_ONLY1["仅推进内存态租约与时间戳 (跳过 SQL 提交)"]
    BATCH -->|"ADR-0258 ROBOGUIDE_TELEMETRY_PERSISTENCE_POLICY"| TELEM{"受保护语义 / 内容变更 / 超 max_persist_interval_ms?"}
    TELEM -->|"是 (Decision / World / SafeState 始终命中)"| SQL_DEDUP["Storage v3 正文去重持久化 + 恢复投影"]
    TELEM -->|"否 (仅时钟字段抖动)"| MEM_ONLY2["跳过冗余历史行 (保留稳态可审计间隔)"]
  end

  subgraph STREAM_B["数据流 B: 多机建图与空间记忆 CAS 融合流 (PR #18/#25)"]
    MAP_A["机器狗 A / B 本地轻量建图包"] -->|"mTLS POST /v1/artifact-uploads + /finalize"| CAS_IN["Artifact CAS :8090 (计算并绑定 SHA-256 + 字节大小)"]
    CAS_IN -->|"POST /v1/maps/{id}/revisions/{rev}"| MAP_CAT["Map Catalog 不可变元数据登记 (Scope / Visibility)"]
    MAP_CAT --> FUSE["Spatial Provider 多机占用栅格融合 (PR #25: CORRELATIVE_ICP / opencv-akaze)"]
    FUSE -->|"上传融合后新图包并发布新 Revision"| CAS_OUT["Artifact CAS :8090 融合地图不可变字节"]
    CAS_OUT -->|"目标节点拉取 GET /v1/artifacts/{sha256} + 本地 SHA-256 校验"| NODE_IMP["目标 roboguide-node 校验并导入 Local EAIOS"]
    NODE_IMP -->|"POST /v1/maps/{id}/replicas (PR #18) & /localization-evidence"| REPLICA["记录受信 (MapSelector, NodeId, Session) 副本与重定位强证据"]
  end
```

---

## 图 4：四种协作耦合模式的控制面 / 数据面分流图（ADR-0026 / ADR-0027 / PR #40）

清晰区分 `MissionPlan` 中四种 `ExecutionCouplingMode` 的协调路径。**核心边界**：Control/Runtime 仅处理状态投影、关系围栏（`relation_fences`）与双端通道就绪度确认；**Mode 4 的高频相对控制与硬件互锁完全在边缘 Local EAIOS 之间直连，Controller 侧数据面零经过**。

```mermaid
flowchart TB
  PLAN["MissionPlan CoordinationContext.coupling_mode"] --> M1["Mode 1: Independent"]
  PLAN --> M2["Mode 2: SequentialHandoff"]
  PLAN --> M3["Mode 3: ConcurrentCooperation"]
  PLAN --> M4["Mode 4: TightlyCoupledCooperation"]

  M1 -->|"标准 DAG"| D1["各 Task 独立 Match / Commit / Execute"]
  M2 -->|"TaskSatisfied + Memory Revoke 门禁"| D2["经 Artifact CAS + import / use 完成跨任务交接"]

  subgraph MODE3["Mode 3 并发协同: 运行时关系与共享视图 (PR #40)"]
    M3 --> REL["Runtime 归约 RequiresActive / SharedSpatialReference"]
    REL --> VIEW["只读共享视图: GET /v1/missions/{id}/shared-view?context_id=... (Fresh / Stale / Unknown)"]
    REL -->|"证据缺失 / 违例 / 重启"| FENCE3["触发 relation_fences 阻断目标 Task 完成"]
    FENCE3 -->|"条件满足后人工或策略对账"| REC3["POST /v1/missions/{id}/relations/{relation_id}/reconcile"]
  end

  subgraph MODE4["Mode 4 紧耦合协同: 控制面通道就绪与边缘直连数据面"]
    M4 --> OBS["Node 采样 Local EAIOS PeerChannelReadiness (gRPC :50051)"]
    OBS --> PC["Runtime 管理 PeerChannel 生命周期: Planned -> Ready -> Fenced -> Closed"]
    PC -->|"双端确认且未过期才下发 Execute"| P2P["Local EAIOS A ↔ Local EAIOS B: 端到端高频控制与断链齐刹"]
    PC -.->|"Controller 数据面流量 = 0 B (超时/断链即 Fence)" .-> P2P
  end
```

---

## 图 5：故障检测、显式 Unknown 隔离与分级恢复数据流（L0–L4 & Safety-Stop）

展示断链、节点崩溃、进程重启或自然设备故障发生后，系统如何避免“未知副作用下盲目重派”，并通过独立安全停机证据核验分支进入 Control 角色恢复（L2/L3）、同 Mission 运行期动态接管（L4 `plan_revision`）或受控人工结案（`reconcile_manual`）。

```mermaid
flowchart TB
  FAULT["异常触发: Wi-Fi 漫游断链 / 进程崩溃 / 租约过期 / 自然设备故障"] --> L0{"L0 Local Autonomy 可局部自愈?"}
  L0 -->|"是 (局部避障 / 本地重规划)"| LOCAL_OK["Local EAIOS 自治完成 (不升级全局恢复)"]
  L0 -->|"否 (跨节点 / 断联 / 故障)"| L1["L1 Integration & Node Service: 重连 / 合并 Outbox / 隔离被覆盖旧注册 (PR #19/#27/#39)"]

  L1 --> AMBIG{"本地 WAL / Runtime 能否确认未产生副作用?"}
  AMBIG -->|"在途未确认 / Controller 重启"| UNK["保守进入 Unknown / Stopping (冻结 Slot 所有权, 严禁盲目重放)"]
  AMBIG -->|"明确 Cancelled / 自然 Failed / 已局部 Completed"| STOP_WAIT["登记 SafetyStop 目的并保留原终态"]

  UNK --> SAFE_OBS["核验独立安全证据: GET /v1/executions/{id}/safety-stop (PR #22/#32/#33)"]
  STOP_WAIT --> SAFE_OBS

  SAFE_OBS -->|"独立 safe-state 确认安全停机 / 进程真实退出"| BRANCH{"恢复与结案决策分支"}
  SAFE_OBS -.->|"缺安全证明或超时"| HOLD["保持 ManualEscalation / Mission Hold (拒绝重派与自动续跑)"]

  BRANCH -->|"L2/L3 同计划角色替换"| RECOVER["POST /v1/executions/{id}/recover -> Control Match / Commit / Rebind 新 Attempt"]
  BRANCH -->|"L4 运行期动态重规划接管 (PR #33)"| REV["GET /v1/runtime-plan-work -> MI Worker Claim + Review -> POST /plan-revisions -> 新 Group 接管"]
  BRANCH -->|"人工取消对账结案 (ADR-0232 / PR #17/#40)"| SETTLE["POST /v1/executions/{id}/safety-stop/reconcile (reconcile_manual 支持 Cancelled / Failed / Completed)"]
```

---

## 6. 关键机制源码映射表

| 图示环节 | 代码实现位置 | 关键版本 / 约束 |
| --- | --- | --- |
| **Controller HTTP 路由与权限** | [`apps/integration-server/src/controller_http/server.rs`](../../../apps/integration-server/src/controller_http/server.rs) | 含 `/validate-plan`、`?timing=`、`/shared-view`、`/relations/{id}/reconcile`、`/plan-revisions`、`/safety-stop/reconcile` |
| **Artifact CAS 与地图目录** | [`apps/integration-server/src/artifact_http/server.rs`](../../../apps/integration-server/src/artifact_http/server.rs) | `:8090` mTLS、`/v1/artifact-uploads`、`/v1/maps`、`/replicas`、`/localization-evidence` |
| **Node Outbox 与漫游重连** | [`core/node-service/src/service/mod.rs`](../../../core/node-service/src/service/mod.rs) | `CoalescedStateOutbox`、`ExecutionSnapshot`、HTTP/2 Keepalive（PR #19/#27） |
| **Liveness 与遥测落盘瘦身** | [`core/state/src/node.rs`](../../../core/state/src/node.rs) · [`core/orchestration/src/integration_bridge/telemetry_persistence.rs`](../../../core/orchestration/src/integration_bridge/telemetry_persistence.rs) | ADR-0257 稳态跳过 SQL、ADR-0258 `ROBOGUIDE_TELEMETRY_PERSISTENCE_POLICY` |
| **Storage v3 去重存储** | [`core/state/src/event_log.rs`](../../../core/state/src/event_log.rs) · [`core/state/src/archive_prepared.rs`](../../../core/state/src/archive_prepared.rs) | `controller_v3_events` 表与事务性正文引用去重（ADR-0244） |
| **多机占用栅格融合** | [`integrations/spatial-provider/roboguide_spatial_provider/shared_map_job.py`](../../../integrations/spatial-provider/roboguide_spatial_provider/shared_map_job.py) | PR #25：`correlative-icp` / `opencv-akaze` 配准与不可变融合发布 |
| **安全停机与人工取消结案** | [`apps/integration-server/src/application/safety_stop.rs`](../../../apps/integration-server/src/application/safety_stop.rs) · [`settlement.rs`](../../../apps/integration-server/src/application/safety_stop/settlement.rs) | PR #22/#32/#33/#40：`SafeStateObservation` 与 `reconcile_manual` |
| **Node 私有计划前检与时间窗** | [`core/node-service/src/preflight.rs`](../../../core/node-service/src/preflight.rs) · [`core/orchestration/src/mission/timing_preview.rs`](../../../core/orchestration/src/mission/timing_preview.rs) | ADR-0255/0256 `complete-sources/v0.1`、ADR-0259 `?timing=v0.1` |
