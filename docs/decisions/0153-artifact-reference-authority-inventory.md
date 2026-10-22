# ADR-0153：Artifact 原引用 authority 清单与旧恢复工具 fence

- 状态：Accepted
- 日期：2026-10-22

## 决策

ADR-0152 的 Archive 生产者必须能在删除事件前缀之后，继续拒绝省略原引用的旧备份。
仅增加 application wrapper 不足以做到这一点：旧 CLI 可能把不认识的空 tail 当成
无 Catalog，从而复制 SQL 却遗漏 Archive 内容。显式 Artifact storage v3 保留原
owner/inode，使用持久升级 intent、原 owner/fence/maintenance header 及 mandatory
`reference-authorities-v1` 清单；v1/v2 binary/CLI 不能把它当旧格式打开。

Controller wrapper v49 在原 SQL checkpoint 事务引入原随机软件 store identity、
Archive journal marker 和完整空 journal。已接受 v49 缺失这些记录时拒绝，不自动修复。
实际 Controller 对 v3 CAS 在开放请求前持久登记该原 store identity。以后所有引用
均属于这份原 authority；多次登记只核对同一原内容，partial 登记保守保留原 consumer。
清单不是访问 grant，不能赋予 tenant、Node、物理设备或执行权。
清单使用不可缩减的有界持久 head；先持久 pending consumer，再原子更新 head，
最后发布原 consumer 行。head 中已知成员缺行即拒绝，partial 登记作为已知消费者保留。
缺 mandatory head 也拒绝，不能通过扫描空目录把原消费者变成无引用。

显式 recovery v0.12 取得 Controller、Artifact、Archive 全部原 writer 租约；必须声明
每一个已知 reference authority 及其原 SQL，不能用同 digest 的另一 owner 代替。
Archive component 保留独立 `archives-v1` 内容及实际 root writer。pending 原命令继续
保留 source-owner CAS；已验证 compact 只退休该 source pin，接收 CAS 的原 Archive
reference 继续独立保留。归档命令重试只读核对，不复制，不刷新原 kernel deadline。

Bundle v0.4 明确保存有界的原目录清单，包括嵌套接收 CAS 的 mandatory 空目录。
校验时缺目录或增加未声明目录均拒绝；restore 仅在全新目标按已校验清单创建它们，
不能在原 bundle 上自动补目录。恢复 receipt v0.12 保留原 source/target/authority。

## 验证边界

实际零 Node 的 Controller/mTLS/CAS/SQL/CLI 进程验证原发布、SQL 失败和精确重试、
重启恢复 Catalog、冷恢复、缺内容/consumer/目录及旧 binary/CLI 拒绝。原历史预算与
authority identity 在重启和恢复后保持；接收归档不由源端有同 hash 字节代替。
故障注入和 parser fixture 不产生设备事实。Artifact GC、Raft Snapshot producer、
在线多数派内容/升级/跨域，以及 N1–N7 其它验收仍为独立必需项。
