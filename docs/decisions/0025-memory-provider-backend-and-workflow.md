# ADR-0025: Memory Provider Backend and Node Workflow

- 状态：Accepted
- 日期：2026-09-03
- 影响范围：Domain、Node Service、Artifact data plane、Memory catalog、Extension Contract

## 背景

ADR-0024 已定义五类 Memory manifest、共享发现目录与选择性交换，但 Node Config v0.5 只注册
provider metadata。节点无法通过统一边界调用异构 Local EAIOS 的 discovery/export/import，
也没有参考 backend 验证完整链路。`ExecutionGroup` 同时出现在 provider scope 和 live memory
scope 中，容易把运行时身份错误固化进静态节点配置。

## 决策

### 1. Local Memory Provider 是节点侧操作合同，EAIOS 保留权威

Node Service 提供统一 Local Memory Provider contract，操作为 metadata discovery、immutable
export 和 selective import。Node Config v0.6 可为每个 provider 声明可选的固定路由
`discover`、`export`、`import` workflow，继续复用 HTTP、dynamic gRPC、MCP Local Integration
Engine。workflow 是真实 EAIOS integration boundary；真实 EAIOS 保留 Memory semantic authority、
backend storage authority 和内部表示，不要求采用 RoboGuide filesystem、JSONL 或数据库。
v0.5 仍可启动，但 provider 只有 metadata 能力。

Node Service 内部另设 `LocalMemoryLedger`；`FilesystemMemoryLedger` 保存 immutable manifest
objects，并据此重建 JSONL metadata index，用于 Node 侧幂等、fallback discovery 和测试。配置
真实 EAIOS workflow 时，它是旁路 ledger，不是 Local EAIOS provider；真实 import 成功后只记录
manifest，不复制 payload bytes。某项操作未配置 workflow 时，它才作为 workflow-free reference
backend fallback，并可为 import 保留本地 bytes。配置字段 `storage_directory` 只指定该
ledger/reference fallback 和受控 export handoff root，不能解释为真实 EAIOS 存储位置。Index 可
删除重建，不是 Memory authority，也不引入全文、向量或中心化索引。

`discover` workflow 的返回值明确限定为 provider 已授权 RoboGuide 发布的 immutable
publish-eligible Memory 集合，而不是 Local EAIOS 中全部 Memory。Node Service 只传入查询/上下文、
校验 manifest 与 live scope，并执行 export、Artifact upload 和 catalog publication；它不做
promotion、排序或从本地 Memory 中自行挑选共享对象。没有 workflow 时，Node ledger fallback 只
暴露此前已记录的 manifest。

### 2. Provider scope 与 Memory scope 分离

provider declaration 的 scope 是静态最大能力：Local 或 Global。manifest 的 scope 是具体
Memory 语义，可为 Local、某个 ExecutionGroup 或 Global。ExecutionGroup identity 必须由当前
ExecutionCommand/TaskExecution 的 live `group_id` 注入，并在节点操作时精确匹配；不得写入
静态 node config，也不因 Node rebind 改变。

Node Protocol v0.3 为 wire compatibility 保留数值 `EXECUTION_GROUP=2`，但 v0.6 registration
明确拒绝它。历史 checkpoint 中若存在旧 static Group provider scope，restore 时一次性归一化为
Local 最大范围并丢弃 GroupId，避免迁移时扩大共享权限；节点按 v0.6 重新注册后才恢复其显式
声明的最大范围。具体 manifest 仍必须通过 live Group context 校验。
`Local` manifest 只能在其 owner Node 内导入；跨 Node exchange 必须使用逻辑
`ExecutionGroup` 或 `Global` scope。

Scope 是 semantic consumption boundary，Visibility 是 discovery/exchange policy，Placement 是
provider-qualified replica evidence，三者不得互相推导。尤其 `Local + Discoverable` 不冲突；
metadata 可进入共享 catalog，但其他 Node 无权消费内容。Artifact reference 仅标识共享 CAS bytes，
不证明任何 Node-local import；`(NodeId, ConsumerProviderId, status)` 才描述本地 placement。

### 3. Memory、Artifact、Index 各自保留边界

- Memory 是带 owner、kind、scope、schema、provenance 的 semantic object；
- Artifact 是 content digest、size 与 opaque bytes；
- Index 只加速 provider-local metadata discovery，可删除重建。

Node 主动 discover/export 并通过既有 Artifact HTTP CAS 上传 bytes、发布 manifest。消费者侧
engine 只对调用方明确给出的 immutable revision 下载并校验 digest/size，再调用本地 import
workflow，最后发布 Staged/Imported/Rejected evidence。replica 请求携带 exact consumer
provider identity，Controller 按接收 Node 的当前 provider contract 和 session 做 admission；它不
重新依赖生产者的当前 registration。该 identity 进入 durable event、projection 与 API，replica
key 为 `(MemorySelector, NodeId, ConsumerProviderId)`，同一 Node 的多个 provider 独立推进且互不
覆盖。Node Protocol 不新增 Memory payload message，Runtime 不保存 Memory，State 不保存 blob，
Artifact Store 不解释 Memory。

相同 immutable selector 的 provider export/import workflow 必须幂等。Artifact digest/size staging
是公开 selective import 的前置条件；Local EAIOS 返回结果不确定时只保留 Staged 并允许重试，
不得猜测 Imported 或 Rejected。幂等契约确保重试不产生第二个语义 revision 或重复副作用。
节点先检查 provider-owned immutable selector；本地已经成功导入时不会再次 staging 或调用 Local
EAIOS import。Imported evidence 单调保持，后续无效尝试不能将它覆盖为 Rejected。
真实 EAIOS 操作成功但 ledger 写入失败属于 outcome 已发生、RoboGuide 记录未完成的可重试状态；
不得用 ledger failure 否定 EAIOS 结果，也不得假设重试不会再次抵达 EAIOS，因此 workflow 的
selector-level idempotency 仍是必要合同。
event payload codec v7 开始要求 `consumer_provider_id`；旧 v6 replica event 无法可靠反推 provider，
因此 replay 到保留的 `~legacy-v6-unknown` identity，而不是猜测或合并到任一当前 provider。

### 4. Spatial Memory 保持强类型验证

`roboguide.spatial-memory/v0.1` 继续只能通过 `/v1/maps` 和 localization evidence 链路发布与
验证；generic `/v1/memories` 不能接纳该 typed schema。通用 Memory provider 不替代 map
anchor、format、lineage、digest 和 strong localization verification。

### 5. 失败只产生 evidence 与 fence

provider、CAS 或 catalog 的未知写结果由节点保留为可重试/需协调状态，不直接把 execution、Task
或 Mission 标记失败。Control 仍拥有 recovery/rebind 决策，Runtime 仍只拥有 live execution
identity、ordered facts 与 fencing。

## 后果

- 框架得到可运行的异构 Memory provider 与显式 data-plane exchange primitive，而不强迫
  Local EAIOS 采用 RoboGuide 存储格式；
- filesystem/JSONL 只承担 Node ledger/reference fallback，不成为真实 EAIOS Memory authority；
- ExecutionGroup scope 已保留逻辑 Group identity 并具备 Node-local invocation validation；完整的
  distributed authorization/handoff 与跨 restart/rebind authority 留待 Control/Runtime/Protocol 设计；
- metadata discovery 与 bytes availability、import success、typed Spatial verification 明确分离；
- 第一版保持单机 provider backend 和 Controller catalog，不承诺 HA、复制、GC 或查询排名。
- Node Protocol v0.3 尚无 Controller -> Node selective-import command；当前完成的是显式 engine
  operation 与 data-plane integration chain，不把 catalog discovery 当作全量复制授权。该命令的
  durable acceptance、重试与 execution association 需独立协议决策。

## 明确不做

- 不建立独立通用图引擎、CRDT、向量数据库、分布式事务或全量复制；
- 不把 provider index 当成共享 Memory catalog；
- 不通过 generic Memory API 弱化 typed Spatial verification；
- 不让 Runtime、State 或 Artifact Store取得 Memory semantic authority。
