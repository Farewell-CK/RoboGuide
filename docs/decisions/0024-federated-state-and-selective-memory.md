# ADR-0024: Federated State and Selective Memory

> ADR-0025 保留本 ADR 的 State/Memory authority 边界，并将当前 Node Config baseline 升级为
> v0.6，补充 provider-local discover/export/import workflow、参考 backend 和 scope 修正。

- 状态：Accepted
- 日期：2026-09-02
- 影响范围：Domain、State、Ports、Integration、Node Service、Controller composition、Artifact data plane

## 背景

RoboGuide 已有 Shared Node State、Allocation State、Runtime execution registry、Mission
orchestration、Control commitment 和 Spatial map catalog。这些对象分别拥有不同事实与决策，
不能被压平为一份所谓 Global Truth。同时，不同 EAIOS 的状态接口、记忆存储、地图格式和
生命周期天然异构；要求它们复制进统一数据库会破坏本地自治，也会把 State、Runtime、
Adapter 和 Artifact 的责任混在一起。

框架需要提供类似 Linux VFS 的共同上层语义：调用者能按对象、语义、来源和 channel 查询，
节点能选择性暴露本地状态与记忆，底层仍可使用 HTTP、dynamic gRPC、MCP、本地数据库、
文件或厂商服务。第一版必须支持地图以外的 Execution、Semantic、Experience 和 Artifact
Memory，但不能引入 CRDT、全量复制、向量数据库或通用一致性协议。

## 决策

### 1. State 是带来源的语义记录，不是单一真值

State v0.1 使用以下正交维度：

- 对象类别：`Node`、`World`、`RoboGuide`；
- 语义：`Desired`、`Committed`、`Reported`、`Observed`、`Derived`、`Belief`；
- 来源：明确的 Node/Local System 或 RoboGuide component；
- channel：来源内部稳定的发布通道；
- 时间：保留 source-local observation time，以 RoboGuide-local receive time 排序和计算 TTL；
- payload：有版本 schema 的有界 JSON，可选 confidence，不跨来源自动覆盖或融合。

精确 `(object, semantic, source, channel)` key 只保留最新接收记录。不同来源对同一对象的冲突
记录并存。`Belief` 必须由显式命名的 belief provider 产生；State 不因看到多个 Observation
自动制造 Belief。

### 2. 统一查询是只读联邦，不是统一写库

Controller 提供统一 State query facade，但按原有 owner 读取：

| 语义 | 当前 owner / read adapter |
| --- | --- |
| Desired | accepted MissionPlan / Mission Orchestration |
| Committed | Control reservations、Group assignments 与 binding lifecycle |
| Reported | Node registration、health 与节点声明的 Reported channel |
| Observed | RoboGuide liveness 和节点声明的 Observed channel |
| Derived | Runtime / Mission lifecycle projection |
| Belief | 显式注册的 belief provider；v0.1 默认无 provider |

`GET /v1/state/providers` 发现 built-in State adapter 与节点 State 声明；
`GET /v1/memory/providers` 发现 Memory provider；`GET /v1/state/records` 使用统一 envelope
和精确 filter。这些 API 不提供通用写操作，不授予 reservation、执行生命周期、
恢复或调度 authority。

### 3. Node 通过 v0.5 配置选择性暴露 State 和 Memory

Node Config v0.5 为每个 State export 固定 local-system owner、对象、`Reported/Observed`
语义、payload schema、TTL、采样间隔、固定 observation workflow 和 JSON pointer。网络
Mission intent 不能修改 endpoint、方法、对象或 schema；部署 facade 负责保证 observation
无副作用，离线 conformance 不宣称能够证明这一外部行为。采样失败只会让上一条记录自然过期，不改变 Node
health、capability readiness、execution lifecycle，也不触发恢复。

Memory provider declaration 固定 owner、kind、最大 scope、最大 visibility、payload schema
和 media type。它描述 discovery/exchange contract，不要求本地存储实现一致。v0.2-v0.4
配置仍可解析，并归一化为空 State/Memory declaration；v0.5 是当前 Extension Conformance
baseline。

Node Protocol 升级到 v0.3：Register/RegistrationUpdate 携带完整 provider snapshot，新的
`StateObservationBatch` 使用同一 management sequence、大小边界和 application-accepted ACK。
v0.2 endpoint 不再接受 session，只返回明确 `FailedPrecondition` 迁移诊断。

### 4. Memory 与实时 State 分离

Memory v0.1 支持 `Execution`、`Spatial`、`Semantic`、`Experience`、`Artifact` 五类，以及
`Local`、`ExecutionGroup`、`Global` scope。每个 immutable revision 的 manifest 保存 owner、
provider、scope、visibility、schema、provenance 和可选 Artifact reference。

- `Discoverable` 可只发布 metadata，内容继续留在 owner 本地；
- `Exchangeable` 必须引用已存在、digest/size 验证通过的 immutable CAS bytes；
- Catalog 是可重建发现与 replica evidence，不转移 semantic ownership；
- 交换由消费者显式选择 revision 并通过现有 Artifact data plane pull，不做全量复制或 P2P；
- replica 只记录 `Staged -> Imported/Rejected` 等节点侧证据，不代表 Task 成功。

通用 Memory HTTP catalog 可发布五类 manifest。Spatial map 继续作为首条强类型验证链路：
`roboguide.spatial-memory/v0.1` manifest 必须使用既有 `/v1/maps`，保留 anchor、lineage、
localization evidence 和 map-specific validation；统一 `/v1/memories` 只提供只读适配视图，
不复制 map authority。统一 list/detail 都可读取 typed map adapter view，typed map 与 generic
Memory 共用 selector namespace，禁止同一 identity/revision 在两个 catalog 中产生歧义。

Node-owned manifest 进入 catalog 前必须匹配当前完整 registration snapshot 中的 owner、provider、
kind、scope、visibility、payload schema 和 media type；replica evidence 只能引用已注册 Node。
通用 Memory mutation 还必须携带成对的 Node/session identity：manifest 只能由其 owner Node 的
当前 active、未过期 session 发布，replica evidence 只能由所记录 replica Node 的当前 session
发布；重连、lease 过期或 Node 不匹配时必须在写入 event log 前拒绝。它是框架内部 ownership
和 fencing，不替代部署层认证、授权或传输安全。

### 5. Authority、持久化与失败边界保持分离

- State projection 拥有 independently attributed records，不拥有 Control 或 Runtime decision；
- Runtime 只路由 execution 与维护 live execution/relation state，不解释领域 State payload；
- Integration 只校验 wire/session/declaration/sequence，Controller composition 才归约并持久化；
- Adapter/Local EAIOS 拥有采样和本地 Memory 实现，不成为第二个 Control、Runtime 或 Catalog；
- Artifact Store 只拥有 immutable bytes，不解释 Memory、Mission 或 execution policy。

Controller inner checkpoint 升级为 v9，保存 source-aware State records；outer server checkpoint
升级为 v10。两者只接受上一个版本进行一步迁移，旧 checkpoint 的新增字段按空 projection
恢复。Memory catalog 由 durable event evidence replay，不进入 Runtime checkpoint。

## 后果

- RoboGuide 可以统一发现和查询异构状态，同时保留冲突、来源、时间和原 authority。
- 新设备只需配置固定采样/声明；旧设备配置可迁移但不会自动获得 State/Memory 能力。
- 五类 Memory 共享最小 catalog/exchange 语义，地图仍保留更强的领域合同。
- Controller 仍是当前 catalog 和 query composition root；本 ADR 不决定 HA、跨 Controller
  replication、认证授权、数据保留/GC 或跨节点时钟同步。
- `Belief` 只有类型和 provider slot，没有 fusion 算法；何时允许 Belief 驱动 Control 仍是
  Q1 的后续 policy 决策。

## 明确不做

- 不建立全局可写 State store 或 last-writer-wins global truth；
- 不引入 CRDT、共识、向量数据库、图数据库或自动 Memory replication；
- 不让 State observation 自动触发 reconciliation/rebind；
- 不把大对象 bytes 放入 Node Protocol、State record、event payload 或 Runtime checkpoint；
- 不把 metadata discovery 等同于读取权限、内容可用性或安全认证。
