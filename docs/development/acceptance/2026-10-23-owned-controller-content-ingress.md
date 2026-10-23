# 2026-10-23 原 Controller 内容入口验收记录

唯一开发分支 `codex/nonrelease-remediation-closure`，提交前置 `b0d1675`。
[ADR-0171](../../decisions/0171-owned-controller-content-ingress.md)和 V2 先定义权威，
本轮实现归属 `core/controller-replication/src/controller_ingress/`；实际证据位于
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。下列原失败分别保留。

## 实际入口

显式 `start_with_controller_content` 使用 parent8/Root4，旧 parent7/direct/metadata
入口不能隐式取得本地原命令权威。Mandatory schema/table/head 即使零输入也必需；
原 journal 绑定完整原候选、实际原 process、TLS leaf、target、原 kernel budget 和
nullable 首次回执。重开核对每条记录仍对应原 process journal；Snapshot 不导入或
擦除它。现行 Domain wrapper、Node Protocol 与设备契约不因此改变。

私有 Unix socket 的实际内核 PID/UID 必须匹配原 supervisor spawn/PID/boot/start
ticks。真实 mTLS 捕获实际叶证书和原链，原 current trust/epoch/transfer fence/
process/预算持续复核。先写原 parent intent，再发 Ready；每个来源再有独立 Root4
Controller-origin intent/pin/Ready，之后才从实际 SDK writer 的 finalized 或 live
Prepared guard 读取。源/接收 FD、独立权限、队列容量随实际 worker 存活，不随异步
等待取消提前释放。完整输入和 Raft metadata 后，同一实际成员在每个 current/joint
set 达完整多数才 ACK；实际首次 receipt 和后续只读完整候选确认分别保存。

独立成员在原最多九成员和原 transport/root 容量内并行，每个输入仍按序。新 child
预算是显式部署声明：缺省 12 秒、支持 100 ms–300 秒，首次记录后不可续；本轮文件
配置声明 **45000 ms**。成员 stream、握手和首帧仍各有 15 秒上界。原 Commit 等待
五秒到期可能留下 Unknown；只读查询必须另核对完整原候选、原 revision 和当前完整
内容 quorum。它不补写第一次结果，也不重发 copy/Commit。

网络 current TLS trust 从原 inode 下的只读 WAL transaction 读取，前后核对原
inode/profile/marker，使用已提交 trust，避免等待 application mutex 挡住心跳。
SIGKILL 后的原 socket 只有在原 writers/完整原 wait history、实际同 user/private
socket type/mode/inode 与明确 ConnectionRefused 同时成立时才清理。普通文件、
symlink、活 listener、未知连接错误或 unreaped/spawning 原历史一律拒绝。

## 已执行的原证据

`owned-ingress-unit-tests-01.log` 的 **3 tests** 通过，覆盖 parent8 空 head 缺失/
旧 writer 拒绝、实际 SQL insert rollback/权限拒绝/原 Unknown 只读重试、真实 PID/
start-tick/zombie/wait。中间完整 Replica 回归 `owned-ingress-replica-tests-02.log`
**100 passed**，维护 quality **82 files** Ruff/format/函数文档和 strict mypy
**363 files** 通过。后续改动仍须最终完整回归，不把中间通过移作最终结论。

原生 `owned-ingress-native-11/owned-source/result.json` 已保留完整实际文件子链：
三个真实 mTLS/Raft infrastructure 进程；原 owned file-contract 子进程实际 PID
`3640224`，真实 Started→Prepared→Completed 走正常 State 端口。Prepared 期间源
public CAS 未发布，接收成员已核对实际完整 bytes。相同成员证书的另一个真实 PID
被 Unix peer 规则拒绝且不产生命令。正常前两次首次回执存在；最后一次实际 SQL
trigger 报 `actual original ingress receipt failure`，原记录保持 Unknown，独立
完整候选/quorum 确认允许原 source SQL 完成，没有重做。

之后隐藏源实际 CAS 并重试原 ID，只返回相同 raw record。源实际 SIGKILL/原 wait
signal **9**，显式新 source process `3641254` 用新原 instance，原 SQL/CAS owner
与 inode 保持；再次实际 wait signal **9**，两条完整原 wait 存于原 journal。
实际 source SQL inode **56700246**、writer inode **56700243**。这个软件文件合同
的 checkpoint 明确是 opaque contract fixture，不是完整 Controller kernel。

该轮随后 infrastructure leader SIGKILL/多数接管通过，原 member 重开遇到残留 Unix
path 的 `Address already in use`，因此 **整体 native-11 失败**，不以已通过子链冒充
完整通过。实际 stale-socket 清理已据此补实现；最终轮次单独保存。

更早 native-01/02 暴露 Controller 来源错误复用了 Replica-only receipt verifier；
后续改为匹配确切原 process/writer/实际源 leaf，Replica 原来源限制保持。native-03
至 07 保存了 debug 管线的原短预算/首次 Commit 回执问题；native-10 进一步暴露
TLS current-trust 同步等待 application mutex、心跳受阻和实际 epoch 丢失。它们均不
计成功，不删 Unknown，也不通过重试旧命令生成成功回执。

## 完成边界

本轮未创建 embodied Nodes、设备、模型或模拟器。受监督 source contract 只验证
通用 State/文件/SQL/CAS/进程链；runnable `controller-replica`/integration-server
完整 composition、内容路由/readiness、joint/catchup/Snapshot/升级/回退/recovery/
GC、带原业务目标的故障矩阵及所有剩余 P1–P7 继续实施。不能从这些软件进程事实
推断设备停止、跨主机 RTO/RPO、物理连续性或 SoftwareComplete。

## 最终原生轮次

`owned-ingress-native-12/result.json` **整体通过**。该轮完整执行上述文件源、真实
same-certificate/other-PID 拒绝和实际 SQL receipt trigger；前三个原命令的 first
receipt 分别为 **存在、存在、Unknown**，原预算均为 **45000 ms**。两个实际
owned source PID 为 **3644943 / 3645731**，其原 wait signal 均为 **9**，保持
独立原 instance。source SQL inode **56700354**、writer inode **56700351** 在
显式原 source 重开后不变。

三个基础成员的实际完整集合为 **211 / 227 / 239**。逐成员隐藏实际 qualified file
后，只计完整 metadata+bytes 的成员；失去同成员完整多数时拒绝，恢复原同 inode
文件后又得到完整三者。原 leader 实际 SIGKILL/wait **-9** 后，**227 / 239** 在
实际更新 epoch 提供完整多数；原 **211** 进程重开后，残留 socket 经原 writer/
原 wait history 与实际 ConnectionRefused 检查清理，完整三者恢复。

四个基础进程实际 waits：**3644893:-9 / 3644897:0 / 3644904:0 / 3646004:0**。
原 parent SQL inodes **56700335 / 56700340 / 56700345** 重开保持，完整原 accepted
candidate/source writer/origins/recovery/预算保持，未因新 epoch 重写原 issuing
epoch。原 source/byte/SQL/配置/exchanges 和所有 native waits 均在该目录分别保留。

该轮冻结 executable SHA-256：

| 实际 executable | SHA-256 |
|---|---|
| content-network-probe-adr0171-12 | db334c774474497a1aec4db20b0cf777c14cefda88d0f351ace92cb119c8face |
| owned-content-source-probe-adr0171-12 | 174db7e60302affcb9cb6579a9b72c7868a0527e9a308ac53b649aec8cc43f2d |
| content-stream-probe-adr0171-12 | 8a763de90e6759c616ecf5326e61fa2f5d139e6b5721a1359fa427fa9a0389ca |

这是 core 与明确标明的实际 owned file contract，不是完整产品 HA。CI 已加入同一
实际零 Node 门禁。最终全库检查和后续 runnable composition 仍独立记录，原失败
不因本轮通过而删除或改写。

## 最终回归

`owned-ingress-workspace-tests-final-02.log`：全库 Rust **1068 passed、2 ignored**。
全部 workspace targets/features Clippy、私有 Rustdoc、fmt、维护 quality 的 **82 files**
Ruff/format/函数文档、strict mypy **363 files**、双语 strict MkDocs 与网站 **9 tests**
通过。原 `owned-ingress-workspace-tests-final.log` 保留新增测试只提供单成员 bootstrap
而被原至少三成员格式拒绝的失败；修正为三个明确标明的存储契约成员后，实际持锁/
未提交 trust 删除窗口通过。测试未改原格式/信任门禁，也不把这些 pins 当真实 TLS
证书；真实证书由前述 native gate 独立提供。

`owned-ingress-legacy-network-final/result.json` 也通过，使用同一冻结 core binary
走原 parent7、实际已停止且全部 reap 的原 Controller/CAS、三个实际 mTLS 进程、
缺 bytes 拒绝、SIGKILL/更新 epoch 多数和原 inode 重开。新的 parent8 入口没有隐式
重解释 parent7 或解除其旧 application RPC fence。原 source 证据保留自己的软件
Node 历史，不由复制重新产生可用性、use 或设备事实。
