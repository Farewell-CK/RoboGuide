# ADR-0142：本地 Memory 内容引用生命周期

状态：Accepted，2026-10-21。

## 背景

Memory provider v3 已包含 `content_references` 表，Active Map GC 与 ContentReclamation 会读取
活动行；但没有受维护的写入/释放协议。Execution、lineage、replica、snapshot 与 archive
声明无法经由 provider 接口持久登记，导致 GC 门禁不能完整消费本地调用者的引用。

## 决定

`--enable-content-reclamation` 同时启用私有 `/v1/content/references` 协议。它只允许配置的
same-UID Unix socket，接收闭合、有版本、绑定 provider 与持久 instance 的 JSON。acquire 必须
声明已有且当前未 tombstone 的 CAS digest、实际 byte size、受支持引用类型、owner、唯一
reference ID 与正 generation；provider 会在数据库 writer mutex 下重新核对实际文件，再用
单个 SQLite 事务持久化。release 必须提交完全相同的 digest/type/owner/reference/generation。
查询为只读，返回原 backend 与当前 generation/state。

`reference_id` 对一个 generation 永不复用。相同 acquire 可幂等重放；release 可幂等重放；
释放后的旧 acquire 重放只返回 `released`，不能让引用复活。新的生命周期必须使用新的
reference ID。旧 generation、其他 owner 或修改过的内容身份不能释放原引用。provider 对
活动引用历史设 16384 行上限，超限拒绝新写入，并在 GC 枚举超过界限时 fail-closed。

Provider 自己已验证的 current-use grant 也会登记 `use_lease` 引用，按精确原 import 与当前
generation 建行；generation 续期只保留当前一代活动。durable revoke 后释放该 lease 的所有行；
重复命令会对账该副作用。backend restart 会先 fence 所有本地 lease，再释放 use-lease 引用。
restart fence 仍不是 Controller 原始 revoke receipt，因此不能单独通过 ContentReclamation 的
original-revoke 门禁。

SQLite v3 原有表已包含全部持久字段，因此不改变数据库/恢复 schema。GC 继续独占最终删除权，
活动引用会阻止 tombstone 和 collect。此协议记录的是本地 provider 声明；same-UID Unix
边界不构成跨主机身份，不证明远端副本、Controller catalog、archive 全局清空，也不证明设备
安全或物理连续性。

SIGTERM 触发 Local EAIOS worker 清理后被主服务捕获为正常退出；server close 等待真实请求
worker 排空，最后关闭 provider SQL 与 CAS owner。

## 验收

真实 provider 进程门禁以文件 import、私有 Unix peer、两次完整服务重启、reference acquire/query/
release、重放与 SQLite 独立读取核对原状态。测试创建零 Node、零设备、零模型、零模拟器。
单测另核对活动 reference 会阻止 reclamation tombstone，且仅 exact-generation release 后可继续。
具体结果见[本地 Memory 引用生命周期验收](../development/acceptance/2026-10-21-memory-content-reference-lifecycle.md)。
