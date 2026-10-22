# State、Memory 与内容数据面

## State：可溯源的当前证据

State 记录对象、Desired/Committed/Reported/Observed/Derived/Belief 语义、source、channel、schema、source time、receive time、TTL 与可选 confidence。不同来源独立保留；统一 API 按原权威只读联邦查询。Control 根据显式 tenant/source/schema/TTL/conflict policy 消费证据。

Belief 需要具名 producer 与原 lineage。confidence 不能被当成方差，重复输入与重启不能刷新 TTL。Source 时钟与 Controller receive time 不跨主机直接比较。

## Memory：内容与使用生命周期

Execution、Spatial、Semantic、Experience、Artifact 内容由原 provider 保有。catalog 提供 discovery；Artifact CAS 保存不可变 bytes；接收端 provider 保存 import/placement/use evidence。配置 EAIOS workflow 时，Node ledger 不偷偷复制后端内容。

| 维度 | 管什么 |
| --- | --- |
| Scope | 谁可以在语义上消费内容 |
| Visibility | 谁可以发现元数据、交换内容 |
| Placement | 哪个 Node/provider 的副本有相应证据 |

`Local + Discoverable` 有效，元数据可见不会扩大消费 scope。

## 从引用到当前使用

1. Plan v0.9 声明语义输入，不指定 backend 路径。
2. Control Bind 后选择 operation-owned receiving provider。
3. Prepare context 原身份、预算、session 与 policy 先持久化；ACK 后原 import 或只读 query。
4. 当前 bytes/instance availability 与 use receipt 成立才可 Execute；另一个 Task 的 Imported 不可复用为新许可。
5. 正常 Task、整组停止和 handoff 按各自范围 revoke；Unknown 保留原 ID，不重复导入。

## 地图、引用与恢复

地图 bytes placement 不等于 active-map 或 localization。active map 保留原 typed provenance、Node、Group、attempt 和目标。worker/use/map/handoff 引用参与原 provider 的保守 GC gate；tombstone 先于 unlink。

静止恢复持有实际 writer leases，核对声明 SQL 和内容。活动引用必须与同包 CAS bytes 一致，缺少必须表、bytes 或存在 tombstone 冲突时拒绝。它不覆盖未声明的全局引用或在线跨主机复制。

参阅[State 模块](modules/state.md)、[Memory/恢复档案](archives.md)与[运维路径](operations.md)。

Controller Artifact 的原 store owner 与 Local Memory provider 分别保留自己的副本责任，同 digest 不合并所有权。原 Catalog/lineage、Snapshot 和 Archive 引用先于复制或发布，Unknown 保留引用；完整原 publication 与精确 SQL receipt 才退休该 source pin。Archive 接收副本独立保留。显式 storage v3/recovery v0.13 校验已知原 consumer、原 cleanup journal/tombstone 与内容，缺已知登记行也拒绝。[原未发布 Archive abort](docs/development/acceptance/2026-10-22-original-archive-abort.md)要求实际全部原 writer 和独立 cleanup budget，保留 partial 与接收引用。源 Artifact 全引用 GC、Raft Snapshot 和在线生产者仍须完成；此前 Archive producer 见[原验收](docs/development/acceptance/2026-10-22-original-content-archive.md)。

[私有接收 owner 的回收](docs/development/acceptance/2026-10-22-private-archive-reclamation.md)进一步接通独立退休：全部实际 writer、空的完整 consumer 清单和唯一原 producer 先于 tombstone/终态 storage v4 seal。独立 GC intent 先于 unlink，原 Unknown 只读；新命令处理剩余 bytes 并保留先前不确定性。v0.14 冷恢复验证完整终态元数据，已退休 history 不再要求已删 bytes。该范围仍不覆盖源 Artifact 全引用或在线 producer 的完整释放与 GC。

原未归属 Catalog 通过[显式接管](docs/development/acceptance/2026-10-22-original-catalog-adoption.md)建立当前保留责任。原 envelope/raw manifest 与实际 CAS 不能由当前主体重写，历史 publisher/tenant 缺失仍为 unknown；当前 admin/TLS/command 独立保存。v0.15 额外核对原 receipt 与同事务 acquisition，任何接管都不新增消费权限。
