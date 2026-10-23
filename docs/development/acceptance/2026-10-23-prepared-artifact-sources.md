# 2026-10-23 Prepared Artifact 作用域验收

唯一开发分支 `codex/nonrelease-remediation-closure`，提交前置 `88b4c96`。
[ADR-0170](../../decisions/0170-scoped-prepared-artifact-sources.md) 与 V2 先定义
原源能力，随后实施 SDK、HTTP 和实际接收路径。所有实际证据在
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`，原失败不覆盖。

## 实际路径与边界

`ArtifactUpload::prepared_replication_source` 在实际 writer 下 fsync、读取实际只读
FD 的完整 hash/size，核对原 uploader 与只读 FD 的 device/inode，随后建立原作用域。
同一 writer lease 的全部 clone 共享 registry，最多 128 个未结束 source；撤销但
仍有 reader 的条目继续占容量。独立 read_at cursor、64 KiB 块和原 writer/GC/FD/
revoke 检查持续生效。原 hash 缓存不能替代实际读取校验，旧 resumed handle 不能
在 scope 内改写，也不能借原 proof finalize 不同源；原 finalize 与 append 串行。

原 guard 或 upload abort/drop 撤销读取；actual reader 继续保留其 FD、writer lease
及 upload 写冻结直到真正关闭。最终 CAS publication 不能恢复已撤销的原 reader。
公开 CAS 仍仅返回 finalized bytes；原 source lookup、Copy receipt、Prepared 元数据
与 Task/Memory use 权限保持独立。SQL 失败保留原 upload/history/预算，临时 guard
在原调用退出时结束，不进入 checkpoint 或重启后的持久许可。

## 当前实际证据

`prepared-source-tests-03.log` 的 **6 项实际文件测试通过**：未注册拒绝、public CAS
未发布、独立读 position、append/finalize fence、abort/revoke/reader lifetime、
原 writer 退出、实际错误 hash/size/inode 和 revoked-but-live 容量。
`prepared-source-tests-01.log` 保留初始测试编译失败；之后修正了测试错误提取，未放宽
实际 SDK 判断。`prepared-http-tests-01.log` 的 **4 项 HTTP 测试通过**，包含实际
Prepared SQL trigger rollback、Completed SQL rollback/restart、既有 foreign CAS
不能代替原 staged bytes 和 scope alias 拒绝。原 HTTP listener 使用真实 TCP。

`prepared-source-native-01/result.json` 通过：真实接收 Root4 和源 upload，原进程
`3500275` 实际 SIGKILL，原 wait **-9**；显式原 upload 重开为新进程 `3500319`，
最终 wait **0**。另外实际 Root 创建与独立 writer-fence process waits 均为 **0**。
源 staging inode **58196194**、writer inode **58196191** 在原进程死后保持。
重启前后实际原 owner/device/inode 相同，新进程仍需显式注册新的原 source guard。

注册前的两次实际接收分别留下原 Unknown，独立 readonly SQL 核对完整 raw row；
之后重复原 ID 只返回原记录，不读取源或补写成功。显式不同 repair ID 才复制实际原
bytes；接收文件由独立 Python 核对完整 hash/size。原源 public CAS 始终 unavailable，
直到显式原 finalize。原 guard 已撤销但 reader 存活时，实际读取和 append 均拒绝；
另一个真实进程不能打开原 source writer。上述不是凭 passed 标记推断的结果。

首轮冻结 `prepared-source-probe-adr0170-01` SHA-256：
`f08a9c977385bf93ee642542c6fc8bf19036fbb2203700c3c09b6078554dc418`；
`content-stream-probe-adr0170-01`：
`c8bbcf87b9569b77b86088b1dcbe561eb66402ca272e23e6088c45cdd576912f`。
原可执行文件、输入、完整 exchanges、native waits、SQL 与 source identities 全部保留。

这是明确标明的本机 source/receive gate，target 仅为声明的 retention context，没有
election 或在线 HA 证明。零 Controller children、零 embodied Nodes、零设备、零
模型和零模拟器。实际 original child/Prepared ingress、内容路由/readiness、完整
joint/catchup/Snapshot/升级/回退/recovery/GC/业务目标矩阵与所有剩余 P1–P7 继续必需；
不能宣布 SoftwareComplete、设备停止或物理连续性。

## 最终冻结与回归

最终 probe 在分配输入内存前检查实际 regular file 的八 MiB 上界，再进行有界完整
读取并核对原大小。冻结 `prepared-source-probe-adr0170-final` 的 SHA-256 为
`eb834a5b2af53725cf6ea1e97878c6060b521664d00f7775318332ef463e3bc9`。
`prepared-source-native-final/result.json` 通过：实际 native waits 为
`3510047:0 / 3510048:-9 / 3510071:0 / 3510072:0`，原 staging inode
`56699408`、writer inode `56699405`，源 identity、完整 Unknown 和原预算保持。
它与前面的 native-01 分别保存，不混用原 PID/inode。

全库 Rust `prepared-workspace-tests-01.log`：**1064 passed、2 ignored**。
全部 workspace targets/features Clippy、私有 Rustdoc 与 fmt 通过；维护 quality
**81 files** Ruff/format/函数文档和 strict mypy **362 files** 通过。双语 strict
MkDocs 构建通过。首个 website unittest 命令缺少维护的 `PYTHONPATH=website`，其
ImportError 原日志保留；随后按 `website/README.md` 的维护命令复测。
`prepared-site-tests-02.log` 的 **9 tests** 通过。
