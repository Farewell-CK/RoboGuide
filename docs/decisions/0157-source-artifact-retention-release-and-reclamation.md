# ADR-0157：源 Artifact 的上传保留释放与全已知引用回收

- 状态：Accepted
- 日期：2026-10-23

## 决策

完成上传是独立的内容所有权；退休 Catalog 不隐式退休上传。新增同租户实际
Artifact admin 或原上传主体的显式退休命令，固定完整原 Completed receipt，
保留当前实际 TLS/稳定主体、首次时间与原 command。原上传结果不改为 Abandoned，
精确重试只读，退休后原 finalize/发布不能再次借用该上传所有权。新事件使用
codec v31/shared wrapper v52；退休不删除任何 bytes，不释放别的生产者。

显式 Artifact storage v5（v4 仍专属终结的私有 Archive）在同原 owner/writer 下
保留不可丢失的 GC journal/head。v3 到 v5 的持久升级 intent 先于任何 header 更改，
原 owner/inventory/maintenance 历史不变；旧 writer/reader/profile 拒绝 v5。

回收为显式离线 OS-admin 操作，显式 recovery v0.16 声明原全部组件并持有全部实际
writer/inode 租约。已知 Controller inventory 不得缩小；遗漏任何已知 owner、原日志、
尚未接通的数据面或不支持的权威形态一律拒绝。不能由调用者的 passed/allow-delete
字段授予权限。

升级或恢复后的旧 Snapshot pin 不能借当前 profile 重造原命令。显式退休入口须提交
原 operation/intent digest 与完整原 profile；其 digest 与原 intent 一致，实际原、新
全部 writer 都成功取得租约，原 destination 仍未发布，才向当前相同 owner 保存独立
v0.2 abort receipt。原 profile/intent/budget 与历史源不改写，缺原故障域租约继续 Unknown。
当前 storage v5/recovery v0.16 fence 早期 reader；原 command 的只读重试不重复退休。

原 CAS 中每个目标依次核对：未退休 Catalog/child lineage、所有 Prepared/未退休
Completed 上传、原 Memory 准备/使用/交接、验证/原目标 CAS 证据、活动 Snapshot、
未终结 Archive 以及其它已知引用。旧未归属 publication 继续保留；过期、进程不在、
元数据复制完成或另一个 owner 有同 digest 都不是释放证明。

运行期 MI 的原 parent/child 声明也保留 Catalog。只在实际 policy 已显式撤销、
Mission 已终结且全部原 switch 为 Applied/Cancelled 后解除这份责任；Completed
还须保留 exact 原目标 gate、独立 receipt 与原 kernel 完成证据。该证据加入原
Catalog 退休 proof digest，当前 use/revoke/import 与 child lineage 仍分别核对。
符合这些条件的 immutable 修订历史不再要求已释放内容，不能因旧字段仍含 digest
而永久保留全部 bytes。

只有完整原闭包不再保留目标时，实际 hash/size/inode、全部原 writer/process identity、
原 kernel 非续期预算与命令 intent 先持久化；原不可逆 tombstone 先于 unlink。
与现有 Local Memory GC 一致，tombstone 永久作用于该软件 owner 的完整 digest，
该 owner 不能重新导入/发布/写回同内容来复活已退休权限。不同 owner 仍独立。

不确定 unlink/receipt 保留 Unknown；相同 command 只观察，不重做删除。新显式
命令可以在自己的原预算内处理原 tombstone 的剩余 bytes，不能改写较早结果。
回收后所有实际读、verify、finalize 与备份/恢复仍检查原 tombstone。释放历史不要求
已删 bytes，任何新的活动引用与 tombstone 冲突则拒绝。

实际门禁须组合原 mTLS 上传/发布/退休、全部租约、活动引用拒绝、真实文件/SQL 故障、
只读原重试、原重启和 v0.16 冷恢复，独立核对 SQL/CAS。设备/外部模型/模拟器为零。
本离线回收不能代替 Raft/online 生产者与多数派内容 ACK；N1/N3 及其余 N2–N7
完成条件不变。
