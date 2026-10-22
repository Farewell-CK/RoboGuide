# ADR-0158：恢复后原 provider Snapshot 的完整 writer 退休

- 状态：Accepted
- 日期：2026-10-23

## 决策

原 provider Snapshot 由原 profile digest、component、provider instance、destination
和 command 固定。已发布 bundle 可经既有 verify/reconcile 在新 root 精确退休；
未发布 copy 在恢复/调整 profile 后不能用新 digest 重建原 command，也不能因为
原预算到期或某个 root 没有进程而释放。当前 `abort-snapshots` 保留原语义。

新增显式 `retire-provider-snapshot`，提交完整当前/原 profile、实际 Memory component、
原 operation 和完整原 request digest。当前入口选择 recovery v0.16；原 profile 保留
实际曾接受的显式 Snapshot 合同，不能重写旧 digest。命令须持有两份完整 profile 的
全部实际 root/use/四 SQL inode/writer 租约；精确相同 kind/root/database 的 writer
只取得一次。Replica/online authority 不由该离线入口推断排空，遇到即拒绝。

实际原/current provider instance 与两个实际 SQL 中的完整原 request 必须一致。
原 source operation 只能为 active/aborted；已发布或曾 released 的 copy 必须使用
完整原 publication proof，不能因 destination 缺失降格为未发布。原 destination
仍不存在且所有原/current writer 已排空后，仅在当前 provider 的原事务执行既有
abort：原 immutable v0.1 receipt 与每个原 generation-1 Snapshot pin 同时退休。
独立历史源 SQL/request/budget/partial 不修改，复制、unlink、启动服务均为零。
新命令的观察 response 使用独立 `roboguide.provider-snapshot-retirement/v0.1`，
包含原 receipts、replayed 与零复制；其中持久 receipt 继续为原 v0.1，不重解释旧 response。

这沿用 ADR-0147 的同一原 abort 权威和 receipt，而非增加旧格式无法表达的新许可；
不修改 Memory SQL v4/writer v0.2、Controller wrapper v52、Node 或应用协议。
原已完成/aborted 记录不能被新输入替换；原请求/instance/profile 缺失、SQL 失败和
writer 不可取得时保持原 pins。相同输入重试只核对已有原 receipt，不复制或续期。

验收须用实际 provider、原 CLI copy/SIGKILL/wait、声明数据的完整备份/冷恢复和
独立 SQLite 读取。两份 profile 中任一实际 writer 活跃、错误原 digest/instance、
SQL fault 以及已发布 copy 都须拒绝。测试不得编辑原 source authority 来制造成功。
该入口不代替 Raft/online producer、全局引用覆盖、设备停止或全部非发布验收。

实际 v0.16 Memory 门禁另发现完整目录校验漏掉 nested SQL 的必要 parent：
`active-map/active-map.sqlite3` 的 `active-map` 由已声明 SQL 唯一导出，应按既有
完整恢复合同保存。校验只增加显式 database/tree 的结构祖先，不接受其它空目录
或旁系子树；不通过删除原目录清单来隐藏缺口。
