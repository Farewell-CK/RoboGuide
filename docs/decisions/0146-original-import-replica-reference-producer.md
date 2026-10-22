# ADR-0146：原 import 生产本地 Replica 引用

- 状态：Accepted
- 日期：2026-10-22

## 问题

本地 ContentReferences 已有接口和 use-lease/FileJobs 生产者，但实际 import 没有产生
Replica 投影。仅在测试中调用 acquire 不能证明真实生产链路。

## 决策

引用由原 MemoryStore 的不可变 imports journal 生产。在原 import intent 的同一事务
写入投影，然后才打开输入或复制 bytes。完整 original 的 canonical SHA-256 定义
保留的 `import:` owner，唯一 generation 为 1；provider instance、原 command、
preparation、Task/attempt 和 bytes 的身份均保留在原 journal。投影不授予读取权限。

启动从原 imports 重建缺失投影。已退休投影不重新激活；tombstone 的原 imports 保持
released。新导入和投影插入的 SQL 失败一起回滚。Unknown、超时和进程退出均不释放。
ContentReclamation 仅消费原 UseLeases 的精确不可逆 revoke 来退休原 Replica 投影；
所有其它引用、worker 与 map 门禁仍独立检查。最终 tombstone 先持久化，再 unlink。

本变化是已有 authority 的保守可重建投影，不添加表、公共权限或新生命周期权威。
Memory SQL v3、Node/Controller 合同与恢复 profile 不变。旧 writer 下的原 imports/
revoke 门禁仍防止误删；本投影不是替代它们的新授权路径。新 writer 拒绝公共引用
命令修改 `replica + import:` namespace。

## 验证与边界

验证真实文件导入前的 SQL 意图、SQL 回滚、原只读查询、重启、原 revoke 和本地 unlink；
实际进程门禁独立查询 SQL/CAS，并创建零 Node、设备、模型或模拟器。
本地 provider CAS 与 Controller Artifact CAS 不能按 digest 合并 owner。
本 ADR 仅完成实际 import 生产者；Catalog/lineage、跨服务、在线 Replica、Snapshot/
Archive 及完整 N1 仍是必需项。
