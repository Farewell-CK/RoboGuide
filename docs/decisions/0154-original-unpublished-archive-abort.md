# ADR-0154：原未发布 Archive 的显式 copier fence 与 abort

- 状态：Accepted
- 日期：2026-10-22

## 决策

原 Archive copy 中断不能靠超时、重新发送、替换 ID 或 lifecycle 字符串释放引用。
显式 recovery-admin abort 只作用于精确原未发布 command：请求固定原 intent digest，
新 cleanup command 在同一原 application writer 内先持久其原调用者、预算和输入。
新预算仅授予 cleanup，不续期 copy、Memory use、Node 或任何设备操作。

实际 source CAS writer 和 Archive root writer 同时被持有，构成受支持同主机软件
copier 的独立排他 fence。证明来自实际已打开的原 writer file descriptions、原
owner/authority、当前 kernel/process 和目录 inode，不能用 JSON 声明代替 OS 租约。
同步 copy 的原 handle 保留 source writer 到 I/O 完成；能重新获取全部原 writer
才能排除该 managed copier 继续读源。它不是 child exit、Node stop 或设备安全证明。
已存在 final publication 时拒绝 abort，必须核对原 publication/SQL receipt。

原 abort tombstone 先于 SQL pin 退休，partial 原文件和接收方引用保持。原 SQL
copy intent、budget、pending/compacted 历史不改写；独立 abort intent/receipt
记录 cleanup 结果。相同 abort command 不获得新时间或替换输入，不再次复制。
回执失败只核对原 tombstone 和原操作。缺 proof、writer 或原输入时保留 Unknown。
被 abort 的原 copy ID 永不重新执行；新的 copy 必须新的原 command、完整重新校验。

shared wrapper v50 / Archive operations v0.2 fence 旧 writer，新增 mandatory 原
abort journal。v49 迁移先核对原 marker、authority 和全部原 commands；原历史及
intent/checkpoint 字节保留，不把 v49 原 checkpoint 改写成 v50。显式 recovery
v0.13 保存和核对两份 journal、source pin 及接收方独立保留责任。其余 v0.12
Catalog/owner/目录与先前 Memory/Replica 合同保持，旧 profile 不遗漏新 authority。

## 验证

实际零 Node 的 Controller/mTLS/CAS/SQL 以原 copy 中断、实际 OS wait、重启和全部
原 writer 验证 fence；原 ID 不重新复制，明确 abort 保留 partial，SQL/回包失败
使用原 tombstone 对账。旧 binary/profile 拒绝，原预算不刷新。已发布归档不能 abort。
接收归档退休/GC、全生产者对账、Raft/在线/跨主机和 N2–N7 仍为独立必需工作。
