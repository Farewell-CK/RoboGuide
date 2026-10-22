# ADR-0155：私有 Archive 接收 owner 的显式退休与回收

- 状态：Accepted
- 日期：2026-10-22

## 决策

接收 Archive 的原 pin 不随源 copy compact/abort 自动消失。新增显式离线维护命令，
由原 OS 管理边界在完整声明 profile 下取得全部原 Controller/CAS/Archive writer，
另取实际接收 CAS writer；源 copy 必须已有精确原 compact receipt。目标必须是该
原 command 独占的接收 owner、唯一原 Archive reference、完整当前 bytes，且实际
consumer inventory 为空、没有其它 Snapshot/Archive 操作或 staging reader。
未知 consumer、pending copy、缺原数据、预算不足或旧不支持格式全部拒绝。

原命令、实际 UID、显式保留决策 reason、完整 copy/publication/owner/input identity、
实际 writer inode/kernel/PID/start ticks 与原非续期 budget 先持久化为退休 tombstone。
随后将同一原 owner/header/fence/inventory identity 密封为只读终态 storage v4，
旧 v3/旧 CLI 不能读取或重开此 owner。该格式不是普通可写 v4 CAS，也不允许重新
登记 consumer 或重发原 copy。partial metadata 保守 Unknown，不删除内容。

GC 是独立原 command/budget：核对上述 tombstone、当前所有原 writer/空 inventory，
先持久原 GC intent，必要时完成同一不可逆 seal，再逐个核对原文件与 inode 后 unlink。
退休 tombstone 始终先于 unlink。相同 command 重试只观察，不能续期或重复效果；
新的显式 GC command 可处理先前剩余原 bytes，不刷新退休或内容使用许可。每份结果
分别保留 observed missing、实际本次 unlink 和 Unknown，不推断进程崩溃后的效果。

显式 recovery v0.14 验证完整终态 owner/tombstone/receipt 与原 publication；已退休
history 不需要已删除内容，但 metadata、原引用及完整 mandatory 目录仍必须存在。
v0.13 及以前拒绝终态格式。普通源 Controller wrapper v50、Node/Memory 合同不变；
源端成功历史不等于接收内容仍 available，旧 copy 精确重试不得重新复制。

此路径只负责实际私有接收 owner 的独立退休/回收。存在已知 Controller consumer、
其它生产者或 pending copy 时，必须等待完整各生产者释放/对账合同，不能靠请求声明
忽略。源 Artifact 全引用 GC、未归属旧 Catalog、Raft/online 及 N2–N7 仍须完成。
实际门禁使用原零 Node Archive gate 产生的 SQL/CAS，再通过 CLI、OS 锁和独立文件
观察验证；合成 parser fixtures 不能代替原 producer 或设备/跨主机证据。
