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
