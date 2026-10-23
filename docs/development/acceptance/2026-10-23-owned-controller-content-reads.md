# 2026-10-23 原 Controller qualified 内容读取

唯一开发分支 `codex/nonrelease-remediation-closure`，前置 `0dd015b`。
[ADR-0176](../../decisions/0176-owned-controller-qualified-content-reads.md)和 V2 先定义
原 child/来源/用途/读取生命周期；实现为 `controller_ingress/content_read.rs`、
`read_client.rs`、完整内容来源选择和 Artifact HTTP/Catalog composition。

## 软件行为

显式 `ReadContentV1` 只通过原 Unix peer PID/instance/boot/start ticks 和 mTLS
连接。完整已提交 target 先核对；Root 的实际 reader intent/pin 先于实际打开/hash/
payload。原 reader authority digest 绑定 process/leaf、target、qualified input、
明确用途和应用 request context digest。原 FD、Root/parent leases 和 queue capacity
保留到实际 worker 结束；取消等待不能补造 closure 或释放独立原 pin。

每个有界 64 KiB chunk 检查原时间预算/current epoch/membership、当前完整 SQL 中的
同一 qualified retained/published source。原 head 与预算不刷新；无关新应用提交
不使读取失败，来源退休、丢失、含糊、撤权、epoch/成员改变仍拒绝。客户端独立核对
原 header/process/context、实际返回 size/hash 和 Root 原 close receipt，失败后原
handle 不可继续。未确认原读操作不重开。

公开来源限定为当前已发布/Completed 且未退休的原引用；纯 Prepared input 即使
已有完整多数 bytes 也不能公开。同 digest 的两个原 owner 不含糊合并。独立 startup
`RetainedIntegrity` 只验证当前精确 retained dependency，不发布本地 CAS、不改
Catalog 原 owner/引用/历史，也不增加 Node/Memory use。此入口不完成 Prepared。

Artifact HTTP 先执行原实际 principal/ACL/scope/import/current-use 检查，每个返回
chunk 前后复核。仅 `ArtifactNotFound` 允许 fallback，本地损坏/冲突不走回退。
新的 Controller 保持自己的 Artifact owner，原 Root 数据保持 source owner，
字节直接经原受权 channel 返回，不写入新的本地 Artifact CAS。

## 实际证据

`owned-content-reads-tests-final.log` 的相关 **108 Replica + 276 integration-server**
测试通过，含 pure Prepared/含糊 owner 拒绝、原实际 mTLS/Root 和无关新 checkpoint
提交后保持同一 retained published source。相关 all-targets/features Clippy、
maintained checker Ruff/strict mypy/函数文档通过。更早相同组件回归和完整旧证据保持。

`owned-content-reads-native-01/result.json` **通过**：三个真实 supervisor、真实
integration-server、mTLS/SQL/Root、零 embodied Nodes/设备/模型/模拟器。
原实际上传 **131101 bytes**，三份独立完整 bytes；空闲 revision 不增长；原 child
SIGKILL/wait 和同 member/inode 唯一 successor 读取仍通过。

原 **307** supervisor 随后实际正常退出 **0**，原 child 由原 supervisor 实际 reap。
**337** 在实际 **term 2** 启动新 Controller PID **3900603**，HTTP 返回原完整 payload。
原 Artifact owner 为 `artifact-store:36342f5539d68ac97e40022b8a4aeb1907029624885bec8bf3e1aa568867879a`，
新本地 owner 为 `artifact-store:decb41732d50f2f7d2a9be3e27162cb7f8d51841b6a21c4596e7292a97ad308a`，
读取前后新本地 `objects-v1/sha256/...` 原 digest 文件均不存在。

同一原 evidence 的 `independent-reader-verification.json` 再只读核对实际 Root SQL：
唯一 `child-read:dc996b80-32fd-43aa-97a9-6134cda9697b` 保留原 source owner，实际
reader file inode **56701860**，close receipt 的完整 size/hash 与原 payload 相同。
实际 Root owner 为 `replica-content:58120cd6c53b43fa6890e6a370637767ff883f5c79900bf41667b15ed3caaf36`。
这份独立观察不修改原结果或 receipt，也不是恢复/读权限。三个 supervisor 实际
PID **3898711 / 3898712 / 3898713** 最终均 wait **0**。

冻结实际二进制：

| 文件 | SHA-256 |
| --- | --- |
| `controller-replica-adr0176-01` | `6df6bc4c29b5c51f9d912ef84563a6ccb1ecb44924429fab19160d0b0a865701` |
| `integration-server-adr0176-01` | `63e8a59b7cdd8b97773efee1c56aa10f50127b76abf9b1e63681ab440f0c9414` |

实际命令为 `check_runnable_content_processes.py --idle-timer-check --owner-failover`，
并显式指定上述 supervisor/Controller 和原冻结 `content-stream-probe-adr0175-01`。
原 identity/配置/完整候选/SQL/日志留在
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/owned-content-reads-native-01/`。
后续只增加了相关测试、独立 SQL 回执检查及旧 typed-map 选择兼容；没有复跑不变的
owned-source/Archive/整个文件故障矩阵来重复计数。CI 对最终源码执行维护的实际 gate。

## 剩余边界

本轮原生 HTTP 为实际同主机 loopback 管理入口；私有 parent channel 为真实 mTLS。
HTTP 原细粒度 ACL/use 的相关旧测试保持，全部跨服务 Memory/typed-map、读取中撤权/
队列取消复合故障仍随完整矩阵继续验收。原独立 Root worker/Unknown 生命周期与新
channel 的实现边界保留，不能将未执行的组合写成 native 通过。

本包不证明 Prepared finalization、完整在线 HA/升级/恢复/GC、跨主机 RTO/RPO、
真实物理停止/设备连续性或整体 SoftwareComplete。继续原 Prepared 接管和其余
全部 N1–N7/P1–P7。
