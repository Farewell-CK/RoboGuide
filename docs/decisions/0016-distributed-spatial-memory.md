# ADR-0016：Distributed Spatial Memory Artifact Plane

> ADR-0024 将当前 wire 升级为 Node Protocol v0.3，并把 typed map catalog 作为 generic
> Memory discovery 的强类型 adapter；地图 bytes 仍不进入 Node Protocol。

- 状态：Accepted for Distributed Spatial Memory v0
- 日期：2026-08-26

## Context

RoboGuide 的地图共享属于 State & Memory Plane，而不是 Runtime checkpoint、Task
handoff 或 Node Protocol payload。两个机器狗需要能够在各自独立的 Mission 中发布和
读取同一份地图，同时保留来源、空间锚点、格式和完整性证据。地图文件可能达到数百 MB，
不能复制进事件日志、Execution Group 或 gRPC execution stream。

## Decision

Spatial Memory v0 采用“不可变 Map Artifact + State Catalog Projection + Node Replica”模型：

1. `MapId` 标识逻辑地图；`MapRevisionId` 标识不可变 revision；`ContentDigest`（SHA-256）
   标识实际 bytes。为使 map/revision 可直接占用 Artifact HTTP 的单个 path segment，两个
   ID 只接受 ASCII `[A-Za-z0-9][A-Za-z0-9._:-]*`；`SpatialAnchorId` 不承担该寻址职责，
   保持独立契约。revision 一旦 Published 不可覆盖，新的构建产生新的 revision。
2. 中央 Artifact Plane 使用文件系统 content-addressed store。上传写入 staging 文件，
   finalize 时校验 digest/size 后原子落盘，并在确认前同步 blob inode、目标目录项和 staging
   删除；同 digest 可幂等复用，冲突 manifest 必须拒绝。
3. State & Memory 保存 manifest、来源 Mission/Execution、lineage、固定物理
   `SpatialAnchor`、全局 revision 状态和每个 Node 的 replica 状态。State 只保存可重建的
   metadata projection，不保存 blob，不选择 active map，也不主动驱动 execution。
4. Integration 提供独立的 streaming HTTP Artifact data plane；Node Protocol v0.2
   仍只承载 canonical execution intent 和 lifecycle facts，地图 bytes 不进入 gRPC。
5. Node Service 声明式地把已 Commit 的 map input 下载到受控 execution sandbox，
   校验 digest 后再交给 Local EAIOS；`prepare-output` execution 在完成前把固定 output path
   冻结为不可变本地副本，后续独立的 `publish` execution 只有在上传、finalize 和 Catalog
   publication 均成功后才完成。`artifact_slot` 必须配合显式 `artifact_operation`，不能由
   同一 slot 推断输入/输出语义。路径由部署配置拥有，网络输入不能指定任意本地路径。
6. Producer 与 Consumer 可以属于不同 Mission。v0 要求 Consumer 使用预先分配的
   `(MapId, MapRevisionId)` 静态引用；不在 Runtime 中做动态 output binding 或字符串替换。
7. v0 的空间权威是显式声明的固定物理 anchor。相同 frame 名称不等于相同物理坐标系；
   import 成功后仍需单独执行 `spatial.localization.verify@v0`。
8. 场景中的 `robot-dog-a/b` 是逻辑 Actor，不等于物理节点。实验使用独立、版本化的
   Control placement 配置把每个 Mission/Actor 约束到 `dog-a/b`；该配置只收窄 Candidate
   Set，不进入 MissionPlan，也不替代 Proposal、Commit 或成功 Group Bind 后的 Actor binding。

默认实验流程是两条独立且对称的链：

```text
Mission A: build -> publish(map-a/r1)
Mission B: import(map-a/r1) -> localization.verify(anchor-lab)
Mission B: build -> publish(map-b/r1)
Mission A: import(map-b/r1) -> localization.verify(anchor-lab)
```

这不是 ownership transfer，也不做 map fusion、实时同步或自动选择“当前地图”。任意
节点都可以读取 Published revision；A→B 和 B→A 是同一接口的两次使用。

## Invariants

- Blob 的权威身份是 digest，revision 的逻辑身份是 `(MapId, MapRevisionId)`；两者必须在
  finalize 时一致。
- Domain 构造、serde 解码和 manifest schema 必须对 map/revision ID 执行同一套 path-safe
  ASCII grammar，不能让反序列化绕过寻址约束。
- `Published` revision 永不原地更新；Replica 的 `Imported`/`Verified` 只表示该 Node 的
  本地证据，不改变全局 revision。
- Node 只有在相应本地动作成功后才按 `Staged → Imported → Verified` 单调写入 replica
  evidence；prepared artifact 持久化、artifact publication 或 replica evidence 持久化失败
  时，相应 execution 不得先报告完成。
- Node 的 frozen/staged 文件必须先同步文件内容与目录项，之后才能持久化 prepared marker
  或远端 evidence。`prepare-output` 在首次读取可变 source 前先持久化 execution/binding
  freeze fence；若进程在冻结与 prepared marker 原子提交之间崩溃，重启与 exact retry 都
  保持 `ReconciliationRequired`，不得再次读取已经可能变化的 source。exact finalization
  retry 还必须重新校验本地普通文件的 size/digest，缺失或篡改时不得只凭远端 manifest
  宣告完成。
- Catalog transition 由 durable event append 顺序决定；HTTP composition 在共享写锁内从
  replay high-water 分配单调 receive timestamp，系统墙钟回拨不能使合法后继 evidence 被拒。
- Artifact catalog 的 event batch 若 COMMIT 结果不确定，服务立即设置 process-local recovery
  fence：目录读写和 `/healthz` 返回 `503 Service Unavailable`，不继续暴露可能过期的内存
  projection；重启后由 durable event log 重放恢复。该 fence 不宣称跨进程 HA。
- 中央 CAS root、Node cache root 和静态 binding 路径都逐级拒绝 symlink/非目录，并以
  no-follow 文件打开约束叶子；词法 `..` 检查本身不构成受控路径边界。
- 事件日志记录 evidence 和 catalog transition，不承载大文件；checkpoint 不复制 blob。
- 当前单机 composition root 对每个 controller SQLite 获取 OS 级独占 writer lock；第二个
  Server fail-closed。该约束不是 Leader Election 或 HA 方案，多 writer 留待后续决策。
- Runtime 只路由不透明的 map reference，Control 只决定和提交执行配置；Catalog 不授予资源
  ownership，也不触发 Task。
- 双节点验收必须显式约束逻辑 Actor 到不同物理 Node 并检查 committed assignment；不能把
  确定性 NodeId 排序当作 A→B/B→A 的跨节点证据。
- 当前 v0 不把 Actor recovery 当作物理狗迁移；任一实验狗故障时，其 Mission Group 保持
  Blocked，只有显式恢复原 Node 或未来独立定义的 ActorRebind 才能继续。
- 删除/GC、map fusion、实时增量同步、动态 output binding、认证和传输安全不属于 v0。

## Consequences

- 同一地图可以跨 Mission、Group 和 Task 被显式引用，且 provenance 可追溯。
- 断点恢复只需重新读取 manifest、验证 CAS digest 并重建 Node replica；不会把大文件塞进
  Runtime checkpoint。
- 后续可以把 CAS 换成对象存储或把 HTTP 换成其他 transport，而不改变 Domain、State 或
  Node workflow 语义。
- 若未来需要动态产出绑定或多地图融合，必须新增版本化 Mission/Artifact contract 和 ADR，
  不能向 Runtime 偷渡该职责。
