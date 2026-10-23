# ADR-0160：原内容生产者日志与中断对账

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0159](0159-original-content-quorum-and-raft-reference-lifecycle.md)

## 问题

ADR-0159 的实际正常存储接线已区分 Log/View/Snapshot/Reader pins，但 pin 本身不
包含完整原生产者 issue/预算/SQL source。append/apply SQL rollback、未发布 build、
install 回包丢失会留下安全但无法完整对账的引用。不能从“当前 head 看不到”推断原
copier/worker 已结束，也不能让同 ID 重启取得新 effect budget。

## 决策

采用显式 member-local content producer journal v0.1、content SQL v3/layout v0.3。
原 backend identity v0.1 保持。原 v1/v2 copy/reader profile 的 create/open/升级入口
保留原义；新 `create/open_with_producers` 与显式 producer upgrade 独立选择权限。
旧二进制拒绝新格式，普通 open 不自动获得 producer authority。

对应的实际 parent Raft SQL 使用独立 storage v5/schema v0.5，强制保留本地
producer heads（含确切当前 View/Snapshot 的 operation 与原 record digest）。
缺 head 拒绝打开/对账，不能将其解释为空引用。旧 storage v4 不自动升级；
显式升级须同时持有原 parent 与 content writers、保留旧 pins 的 unavailable
历史并独立校验完整当前 parent。Snapshot 不传递这些 member-local heads。

每个实际生产者在副作用前将原 operation ID、kind、完整 qualified inputs、原
profile/member、真实原 Raft SQL writer inode/device、原 parent epoch/log/Snapshot/
checkpoint/batch identity 与完整 digest、actual Linux kernel/PID namespace/PID/start
ticks、原 issue/deadline 持久化。完整 journal head 与全部独立 pins 在同一 FULL SQL
transaction，不能只新增一种 DTO。原只读 observation 不重新 admit 或续预算。

新 admission 返回仅实际 managed worker 持有的一次 permit，原 Root 与 source SQL
leases 保留到该 worker/handle 真实结束。预算检查先于实际 file/SQL/hash effects、
等待后和 publication。原同 ID 的 Unknown 只观察；新的显式 storage repair 有独立
intent/预算，保留早先 Unknown，不能借它重复 Task/Execute/provider。

不可 Clone 的原 permit 持有实际 source SQL writer 的确切类型，不能用任意 Arc/
caller DTO 替代。managed worker 的真实 scope 退出可独立写入原 close receipt，
仅说明该作用域不再能执行原效果，不证明 publication 或 OS wait。异步/阻塞 worker
必须把 permit/实际 leases 移入其真实执行作用域；取消外层 future 不提前关闭它。
scope 退出写入失败仍留 Unknown；缺这种原 close 时，对账要求原 process absence。

新显式 producer profile 的重型 append/apply/Snapshot 异步入口将真实租约、不可续
原时钟与容量 permit 移入 `spawn_blocking` 的实际作用域。每个实际 store 最多八个
运行或已排队 job，超限在新 intent/effect 前拒绝；原五分钟预算在排队前采样。
同步显式工具入口使用相同容量/时钟，但不声称网络线程可用性。旧 metadata/reader
profile 保持原行为；这不关闭 Replica listener/frame 的独立 N7 预算验收。

actual source SQL 的 exact original log、accepted view 或 published Snapshot 证明
precede Published receipt。真正被 replacement/truncate/purge 的原责任才可退休；
每个 reader/receive/Archive/其它 consumer 独立，父 producer release 不代替它们。
完整 original producer record、原 publication 与 retirement receipt 永久保留在
明确 bounds 内；容量耗尽拒绝，不删除历史或复活旧 ID。

中断对账持有全部实际 original root/SQL writer。已有 exact publication 仅补记原
观察，不复制或重新 Commit。缺 original publication 的 abort 须独立证明原实际
worker scope 或 process incarnation 已结束，并核对确切 parent 原 log/view/Snapshot
全集合不再有该责任；
原 epoch/time expiry、断连、当前 JSON flag 或一个 SQL key 缺失不够。若任一 source/
mandatory head/原 SQL identity/worker proof 不可用，保留 Unknown/pins。实际本地
process absence 与原 OS wait 分别保留，不把一项写成另一项。

readonly 发现原 publication 但缺原 within-budget completion receipt 时，只保留独立
observation，原 outcome 继续 Unknown。后续释放须保留这份不确定历史；不能把
“曾被观察到”改成“原操作在 deadline 内 Completed”或补造原时钟。

Snapshot build/install 有独立原操作；Raft Snapshot bytes 只带原 accepted authority
及完整 metadata；本地 producer reference 留在 member-local heads，绝不导入、清空、续期 receiving member 的 producer/copy/
reader/transfer/child authority。Snapshot replacement 不结束 old reader 或未知
producer。未发布 partial/tombstone 保留；原 ID 不能再次开始一次 copy/build/install。

已有 v2 的原 pin 无完整 producer budget/source，升级不能补造历史。迁移原样保留
raw rows 与独立 legacy inventory，明确 manual/unavailable。显式 adoption/退休必须
由 actual original parent SQL、完整内容/consumer 和全部原 writer 观察生成新 current
receipt；不改写原 ID、source、预算或称其早先已有完整 process proof。

## 实施与验收

先建立实际 Root SQL journal/mandatory heads/leases，再接原 append/apply/build/
install 的实际 managed workers、publication/release 与只读对账。真实 SQL I/O/回包
中断、原 deadline、SIGKILL/native wait、PID namespace、lost rows、old live writer/
binary、partial/restart、Snapshot replacement + independent reader 都必须保留原结果。
实际新 repair 与旧 Unknown 并存，legacy upgrade/adoption 分开验收。

沿用唯一开发分支、真实软件文件/SQL/TLS/owned process gate，0 设备/模型/模拟器。
source process 或 storage evidence 不产生 Node/Task/device stopping、在线 quorum 或
物理连续性。mTLS same-voter ACK、完整 online Archive/GC/recovery、原部署升级
和 N2/N4–N7 仍全部必需；单个内部小步不关闭 N1/N3 或 SoftwareComplete。

ADR-0161 后的 recovery v0.17 只增加 Controller storage generation，保留完整 v0.16
合同；它不包含新 content producer roots。完整 online content recovery 将另行显式
版本化（预留 v0.18），不能把同一数字或已有 cold receipt 重分类成已完成在线接线。
