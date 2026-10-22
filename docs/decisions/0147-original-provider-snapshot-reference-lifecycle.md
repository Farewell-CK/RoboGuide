# ADR-0147：原 provider 的显式 Snapshot 引用生命周期

- 状态：Accepted
- 日期：2026-10-22

## 问题与边界

静止恢复工具已冻结真实所有 writer 并核对 CAS，但 Snapshot 尚未生产持久引用。
进程中断后的引用责任需要原命令和实际发布证明，不能由一个 passed 字段解除。
本决策只增加原 Memory provider 的显式 snapshot 生产者；不将离线恢复改称在线 HA。

## 决策

部署显式启用 snapshot references，升级 Memory SQL v4。迁移先验证原 v3 完整 marker/
表，再由 native DELETE/exclusive transaction 排空旧连接，保留原 instance、imports、
use、worker、map、预算和历史。旧 v3 writer 拒绝 v4；默认 v3 不隐式升级。恢复显式
v0.8，保留 v0.7 的 Replica storage v3 与原进程 proof 合同。

恢复工具持有全部原 root/inode 租约后，按实际 CAS 内容目录冻结本次原 provider、
component、profile digest、命令、目标路径、内容集合、UID 和原 kernel 预算，原 SQL
intent 与所有 snapshot pins 原子提交，然后才复制。已存在未确认原 intent 不允许新
copy，不接受更换集合或预算；缺失/损坏内容、SQL 失败和进程退出均不推断退休。

发布完整 bundle 后，以实际 verifier 检查所有 bytes/SQL 与原 manifest digest，再在
各原 provider 的同一租约下持久化完成回执与 exact-generation release。回复丢失时只
检查原发布结果，不重做 copy。显式 reconcile 对原或恢复后的相同 instance 生效，
绑定原 intent、原 profile、原 command 和 bundle 内原 operation descriptor；不同
owner、同 digest、伪造结果或不完整 bundle 不能解除 pin。

冷恢复原样保留 active intent 和所有 SQL；不自动改写历史或授予当前 use。需要释放
恢复后的原 snapshot pin 时，显式 reconcile 使用独立实际验证过的原 bundle。
保留权不授予内容读取权。任何未知原状态保持引用，原 provider 独占 tombstone/unlink。

未发布操作可显式 abort：先取得全部原 root/inode 租约，确认目标未发布，再在同一
原 SQL 事务中把唯一 command 永久置为 aborted 并退休 exact-generation pins。旧同步
复制在这些租约之外不启动后台/child I/O，因此活跃旧进程会阻止该入口；同命令以后
不能再次 copy，partial 证据保留。这不是跨域 2PC 的单方超时 abort，也不是设备停止。

## 验证要求

实际 provider/恢复 CLI 进程验证复制前 intent、真实 CAS、原命令重放与冲突、SQL 和
响应丢失、进程中断、冷恢复及精确退休；独立查询 SQL，并验证旧 writer/profile 拒绝。
操作不创建 Node、设备、模型、模拟器或物理连续性事实。Controller Artifact、Catalog/
lineage、在线 Replica、Archive 与完整其余整改仍分别验收。
