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

### 1. Local Memory Provider 是节点侧语义边界

Node Service 提供 `LocalMemoryProvider`，操作为 metadata discovery、immutable export 和
selective import。接口不要求具体 EAIOS 使用同一数据库。Node Config v0.6 可为每个 provider
声明可选的固定路由 `discover`、`export`、`import` workflow，继续复用 HTTP、dynamic gRPC、
MCP Local Integration Engine。v0.5 仍可启动，但 provider 只有 metadata 能力。

首个参考 backend 使用 provider-local filesystem immutable manifest objects 与可重建 JSONL
metadata index。它只实现确定性的 kind/scope/provider/owner filtering；Index 可由 semantic
manifest objects 删除重建，不是 Memory authority，不引入全文、向量或中心化索引。

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

### 3. Memory、Artifact、Index 各自保留边界

- Memory 是带 owner、kind、scope、schema、provenance 的 semantic object；
- Artifact 是 content digest、size 与 opaque bytes；
- Index 只加速 provider-local metadata discovery，可删除重建。

Node 主动 discover/export 并通过既有 Artifact HTTP CAS 上传 bytes、发布 manifest。消费者侧
engine 只对调用方明确给出的 immutable revision 下载并校验 digest/size，再调用本地 import
workflow，最后发布 Staged/Imported/Rejected evidence。replica 请求携带 exact consumer
provider identity，Controller 按接收 Node 的当前 provider contract 和 session 做 admission；它不
重新依赖生产者的当前 registration。Node Protocol 不新增 Memory payload message，Runtime 不保存
Memory，State 不保存 blob，Artifact Store 不解释 Memory。

相同 immutable selector 的 provider export/import workflow 必须幂等。Artifact digest/size staging
是公开 selective import 的前置条件；Local EAIOS 返回结果不确定时只保留 Staged 并允许重试，
不得猜测 Imported 或 Rejected。幂等契约确保重试不产生第二个语义 revision 或重复副作用。
节点先检查 provider-owned immutable selector；本地已经成功导入时不会再次 staging 或调用 Local
EAIOS import。Imported evidence 单调保持，后续无效尝试不能将它覆盖为 Rejected。

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
- ExecutionGroup Memory 在 rebind 后仍按逻辑 Group identity 延续；
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
