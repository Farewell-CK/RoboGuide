# ADR-0098：Memory Selective Import Command v0.1

- 状态：基础实现已落地；Controller receipt projection、ACL/retention 和故障演练待完成
- 日期：2026-10-14
- 衔接：ADR-0095、ADR-0096、Node Protocol v0.4、Node Contract v0.6

## 决策

1. Memory discovery 只返回 provider 明确授权发布的 manifest；它不能隐式产生复制或本地导入命令。
2. Controller 通过当前 v0.9 wire 在 v0.4 session 上的 opt-in `MemoryImport` extension 请求一次选择性导入。旧 Node Protocol/Node Contract 不声明该 extension 时保持拒绝。命令必须包含
   `import_id`、`command_id`、consumer provider、完整 manifest JSON、Mission/Group/Task/Role、当前
   execution attempt 和 Controller epoch。`command_id` 固定为 `memory-import-{import_id}`。
3. 命令额外携带 `authorization_digest`，它覆盖完整授权元组、manifest JSON、协议 schema 和
   Controller epoch。Node 在任何 provider workflow、Artifact 下载或本地副作用前重新计算并校验该摘要。
4. Node 只有在注册快照显式声明 `roboguide.memory-import/v0.1` 时才接受该命令。旧 Node 不声明时，
   Controller 路由直接返回 `failed_precondition`。
5. Node 在本地 SQLite journal 中先写入 `Pending`，然后发送 `MEMORY_IMPORT_PERSISTED` receipt，之后才执行
   provider import。完成后发送 `MEMORY_IMPORT_COMPLETED`；确定性身份/provider/provenance 错误发送
   `MEMORY_IMPORT_REJECTED`；本地或传输结果不明发送 `MEMORY_IMPORT_UNKNOWN`。
6. Node restart 会把未完成的 `Pending` 标记为 `Unknown`。相同 import identity、command identity 和
   authorization digest 才能重新进入一次执行；任何证据变化都被拒绝。终态 `Completed`/`Rejected` 不得
   被另一终态覆盖。
7. Memory manifest 的 provider admission、scope、CAS digest、replica evidence 和真实 Local EAIOS
   storage authority仍由现有 Memory/Artifact 边界拥有。该命令只授权导入，不授予 Control、Runtime 或
   Node 重新解释 manifest、选择 provider 或改变 Task/Mission 生命周期的权力。

## 实现范围

- wire：`contracts/node/v0.9/roboguide-node.proto` 增加 `MemoryImport`、`MemoryImportReceipt` 和
  bounded receipt status；v0.4 session 的旧 Node 不声明该 extension。
- Controller transport：`GrpcNodeRouter::import_memory` 检查当前 route、lease、provider identity、
  Node opt-in 和 Controller delivery fence。
- Node：`NodeService` 校验 digest、provenance 和 epoch，调用现有 `exchange_memory`，并把 import identity
  写入 `ExecutionJournal` 的 `memory_imports` 表。
- Node journal：`Pending`、`Completed`、`Rejected`、`Unknown` 状态可重放；重启和 retry 不会复制新的
  import identity，也不会绕过 provider admission。

## 未完成与禁止事项

- 还没有把 MemoryImportReceipt 投影为 Controller 的独立 EventLog authority；当前导入完成的内容证据仍
  通过 Artifact/Memory catalog 的现有事件路径重建。
- Group authorization 的完整 Control/Runtime/Commit/Recovery 事务仍未完成；本命令携带 provenance，
  不代表 ExecutionGroup scoped Memory 的分布式授权已经完成。
- retention、revocation、GC、manifest ACL 和跨站点 Memory replay 仍是后续 gate。
- 不自动融合不同设备、不同高度或不同 frame 的地图；Spatial Memory 继续使用独立 map/revision/frame。
- 软件 receipt 和 journal 不能证明物理设备连续性，也不能替代 Local EAIOS 安全停止或真机验收。
