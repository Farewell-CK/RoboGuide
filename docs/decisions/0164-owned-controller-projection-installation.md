# ADR-0164：原 Controller SQL 的受控投影接管

- 状态：Accepted，实施中
- 日期：2026-10-23
- 前置：[ADR-0159](0159-original-content-quorum-and-raft-reference-lifecycle.md)、[ADR-0161](0161-controller-inode-writer-and-storage-generation.md)

## 决策与边界

实际旧 supervisor materialize 只锁主路径 sidecar，构造临时 SQL 后 rename 覆盖原路径。
真实 Controller 通过 mTLS 接受的原 quota 命令仍被 hardlink alias 上的实际 managed
clone 持有时，旧组合函数仍能将主路径换成空 SQL：原 4 条命令变为 0，alias 留在旧 inode。
这是实际本地组合函数的反例；空 view 是明确测试输入，没有声明它已由 quorum 接受。

接管复用 runnable Controller 的实际 canonical sibling + SQL inode 租约及原生 SQLite
排空/格式检查。目标保持同一个实际 SQL inode，完整事件/Checkpoint/命令及原辅助
recovery 在同一个 SQL transaction 中安装。不再 rename SQL、不删除原 WAL/SHM、不把
遗留临时文件清理当作接管前置。失败回滚保留原已提交内容；空 view 不能清空原数据。

应用 schema/profile、完整 view 和必要原 recovery 在 source writer 前验证。现代 wrapper
缺完整 recovery 立即拒绝；不从本地数据猜测缺失的 Raft Archive authority。声明的
Controller storage legacy/v1 与 child 实际配置一致，v1 不被重新降级为 legacy。

State 的实际安装事务也核对已有原命令为新命令的不可变前缀、Checkpoint 不回退或同 revision
分叉。原 recovery marker 已存在时复用 `ReplicaRecoveryProjection::follows`，保护原 copy/
abort intent、Unknown、receipt、Catalog 与 manifest 历史。缺 marker 的半初始化历史
拒绝成为空初态；完整源数据不能覆盖另一个原 authority，也不能抹掉本地尚未归约的责任。

supervisor 的同步本地投影工作移到一次实际 owned blocking task；同一 supervisor 不排队
并发接管。取消等待不终止原 task 或释放其 SQL 租约。完成后仍需原 epoch/quorum、原
child process journal 和实际 supervisor spawn/register 门禁；本地安装本身不授予 child、
Node、调度、内容读/GC 或执行权限。原 process history、预算与 wrapper/Node 合同不变。

完整 production v0.4 内容网络、同一 current/joint voters 的 bytes+metadata ACK、旧
profile/child migration、routing 与 online failover 继续必需。本地完整安装不把现有
v0.1–v0.3 metadata profile 升级成完整 HA，也不宣告 SoftwareComplete。

## 验收

保留旧实际 Controller/原认证命令、持有 alias 的实际 clone、原/替换 inode、原 rows 和
native waits。新路径必须在旧 clone/实际旧 SQLite transaction、格式/identity 不符时拒绝。
空 view、缺 recovery、旧 journal 前缀/原 Unknown 缺失、SQL rollback 必须保留原责任。
新 legacy/v1 空存储及完整实际原 State 安装、原 CAS/Controller 重启分别核对；不得以
JSON 成功字段、内部元数据测试代替原实际进程证据或全 online HA。
