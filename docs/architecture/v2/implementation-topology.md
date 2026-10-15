# 当前软件实现与部署拓扑

更新：2026-10-15。对应全部非发布软件整改计划；后续目标不视为当前能力。
节点、LocalSystem、能力、操作、State export 和 Memory provider 均由部署配置和正式注册提供。
未接设备的部署可以拥有零个 Node；系统不能自行生成设备或能力。

```mermaid
flowchart TB
  Client[证书主体 / tenant / 幂等键] --> MI[Mission Service / MI / Request + provenance SQL]
  ID[Control 持久身份 / 授权 revision / tenant / 测量] --> Entry
  ID --> PROTO
  MI --> Entry[Controller 用户与高权限入口]
  Entry --> ORCH[Orchestration / DAG / Mission hold]
  ORCH --> CTRL[Control / commitment / binding / calendar]
  CTRL --> RT[Runtime / attempts / durable intents]
  RT --> PROTO[Integration / Node Protocol]
  PROTO --> NODE[配置接入的 Node Service]
  NODE --> LOCAL[部署侧 Local EAIOS / 操作 / 即时安全]
  LOCAL --> OBS[注册中声明的 State / safe-state export]
  OBS --> PROTO
  PROTO --> STATE[来源联邦 / receive time / TTL]
  STATE --> BELIEF[显式 Belief producer / 原始 lineage / 非续期 TTL]
  BELIEF --> GATE[Control 来源策略 / Match Commit Bind Rebind Readiness]
  STATE --> GATE
  GATE --> CTRL
  VERIFY[独立 verifier / 当前 plan + attempts / CAS proof] --> ORCH
  VERIFY --> SQL
  STATE --> STOP[应用 stop 归约 / receipt + terminal + safe-state]
  STOP --> ORCH
  STOP --> CTRL
  ORCH --> SQL[应用事务 / 完整 checkpoint / authority journal]
  CTRL --> SQL
  RT --> SQL
  STATE --> SQL
  SQL -. 显式 HA profile .-> HA[静态三成员 quorum / epoch / materialization]
  NODE <--> ART[Artifact CAS / 内容授权]
  NODE --> MEM[配置固定的 Local Memory provider]
  MEM --> QUERY[原实例 GET query / 实际 bytes 与原始日志]
  QUERY --> PROTO
  Entry --> MINT[持久 Memory intent / 对账 generation 与预算]
  MINT --> SQL
  MINT --> PROTO
  ART --> CAT[Memory / Spatial catalog 与 placement evidence]
  CAT --> SQL
```

Controller 中 Orchestration、Control、Runtime、State 和 Integration 当前共址，责任边界由
Rust 模块及 transport-neutral ports 保持。Mission Service 是独立进程，Local EAIOS 位于
部署侧。默认监听 loopback；非 loopback 入口必须启用对应 mTLS 和证书权限 profile。
默认单 Controller，显式静态三成员 HA profile 使用独立副本 SQL 和 Controller 子进程。
动态成员、Federation、运行期重规划、完整时空/quota 和自动空间融合属于本轮必需目标，
目前仍需按阶段实现和验收。

副作用只在应用事务持久化并取得当前 quorum/epoch 后发出。副本只复制元数据和命令证据，
CAS 内容及 Local EAIOS 执行连续性需要独立恢复合同。Unknown 保留所有权；本地安全仍由
Local EAIOS 掌握。SoftwareProcess、测试合成观察和设备观察的证据等级不能混用。

当前停止软件门禁见 ADR-0102。完整数据记录可由
`GET /v1/executions/{execution_id}/safety-stop` 检查；这个查询不刷新 TTL 或 stop deadline。
身份与 tenant 权威的当前调用链见 [ADR-0103](../../decisions/0103-http-command-reconciliation-and-node-identity-lifecycle.md)
及[身份合同](../../development/identity-lifecycle-v0.1.md)；证书登记不会增加设备 inventory。

State 与 MI 的当前软件路径见 [ADR-0104](../../decisions/0104-state-consumers-belief-and-semantic-verifier.md)
及 [F3 进程验收](../../development/acceptance/2026-10-15-f3.md)。Belief 是命名 producer，Control
只消费显式策略；它不取代原始 State 或成为 Global Truth。持续 verifier 的失效进入 stop/hold，
不能跳过实际停止证据。模型返回字段与配置 identity 均保留，缺失不补成已证明。

Memory 连续性的当前切片见 [ADR-0105](../../decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md)
及 [进程验收](../../development/acceptance/2026-10-15-f4-memory-continuity.md)。Node Contract v0.7 /
config v0.8 显式协商原实例只读查询；导入原始身份先持久化，Unknown 不允许换 ID 重做。
Controller wrapper v31、codec v22 和 Node journal v7 保留版本边界。Imported 历史不能授予
当前可用性租约，Memory 对账不能满足 Task。Group 授权、active-map/GC 和空间计算仍属 F4 必需工作。
