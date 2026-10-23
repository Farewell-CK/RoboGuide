# 2026-10-23 共识绑定的内容传输验收

唯一开发分支 `codex/nonrelease-remediation-closure`，提交前置 `b372d95`。
[ADR-0169](../../decisions/0169-consensus-bound-content-stream-transport.md)先定义新的
网络权威，再接实际 Root4/parent7/mTLS。证据目录为
`/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。

## 原始权威与实际路径

core `ReplicaNode::start_with_content` 必须显式 v0.4、实际持有 writer 的 Root4 与
bootstrap owners。原 root 必须匹配实际 self/profile；首次 Raft membership 带完整
endpoint/owner。原已提交 member data 不能由另一个局部 map 改写。parent7 mandatory
marker 在空库也必需；旧 metadata/direct open 与旧库隐式转换均拒绝。普通 Node/
runnable supervisor 启动入口仍要求其完整原 child 合同，未因该 core 构造器启用。

原 Artifact SDK 的实际 writer device/inode/owner 与候选源完全匹配，先持久本地
source stream 及 pin，再读取源。原 target、预算、epoch 和源权限逐块检查，原 ID
Unknown 只对账，新 repair 必须显式原 ID。源读取与网络接收各有独立实际 managed
reader/worker/pin，原 clock 与八个 queue/running slots 在真实 blocking worker
内保留。异步取消只结束等待/通道，不宣布实际 worker 或文件已结束。

远端必须是实际 mTLS 上的原当前 leader、确切 committed membership 和原 owner。
接收 intent/pin 落盘后才发 Ready，源收到 Ready 后才打开 payload。64 KiB chunk、
两块队列与原 transport/时钟上界持续生效。原完整 target/digest/command/qualified
source/receiver 与实际 receipt 精确对应；重复请求返回完整原观察并跳过源读取。

`commit_content` 先核对实际本地完整 bytes，并通过真实 stream 准备接收方，随后写
完整原候选/recovery/origins 的 Raft metadata。接纳后，每个计数的成员都独立读取其
实际 committed view、全部 qualified files、原 read receipts，并在前后核对同一
metadata/epoch/membership。只有同一批完整成员在每个 current/joint voting set
都满足严格多数才返回 ACK。单个 receipt、metadata 多数或另一个 owner 的相同 hash
均不替代这个门禁；失败返回有界实际成员原因。

最初 try_lock 的权限查询在普通 Raft 写入时过度拒绝，原失败见
`network-content-quorum-tests-01.log`。现在在原路径和实际 SQL inode 租约保护下，
以独立 readonly WAL transaction 同时读取已提交 trust/membership，前后验证同一
inode、parent7 marker 和原 profile。它不等 parent application mutex，也不读
未提交事务；原 epoch、完整 membership 与实际证书有效性仍检查，不把普通 mutex
占用解释成权限撤销或跳过权限。

## 实际软件证据

`network-content-stream-tests-01.log` 的实际三服务 mTLS/1 MiB 分块与两个独立接收方
通过；同一个 command ID 在两个 receiving roots 有独立原 receipt，源文件隐藏时
重复请求不重读。实际 full ACK/缺 bytes 拒绝与原 source writer 路径已进入完整 replica
模块回归 `network-content-replica-tests-01.log`：**99 passed**。另外三项真实 SQL/
inode/mandatory marker 测试覆盖旧打开入口、错误本地 owner 的 append 无效果拒绝和
父 mutex 占用时的实际只读查询。这里单成员 synthetic member 数据仅是格式契约测试，
不声称网络 quorum。

`network-content-native-01/result.json` 通过。它从已验收且实际全部 reap 的原
Controller/CAS 读取真实完整状态，启动三个独立真实 infrastructure processes，
通过其实际 mTLS 与 SQL/CAS 完成同成员 ACK。成员编号由命令行显式声明为
`137,149,163`，不是设备或能力 inventory。三者实际 complete members 为
`137,149,163`；原 leader 被实际 SIGKILL，原 native wait **-9**；另外两者在实际
更新 epoch 提供 `149,163` 的完整多数。原 member 重开后恢复三者的内容 quorum。

四次实际 incarnation waits 是 **-9、0、0、0**；原 parent SQL inode 分别
`56698553 / 56698558 / 56698563`，重开不变。原完整 accepted candidate、source
writer/origins/recovery、预算和原 schema 内容逐字节保留，未改写原 issuing epoch。
独立 Python readonly SQL 与实际 qualified file 的完整 hash/size 核对通过。

首个冻结 native probe 为
`7c5f3e675ea0e959c92d147d6b4e507026451bc11c5252d1b5b5bbd4f8c1d3a7`；
原 storage probe 为
`40ecdf7df3c5d5f901b8321781d728d5018e342839a19f64eee667655b58e423`。
所有原配置、实际 argv、完整 responses、actual waits、SQL 和 source/binary identities
保留在该目录。旧 gate 的失败仍保留，不由后续通过覆盖。

## 继续实施

该结果是 core 的实际网络内容路径，不是全部生产接线。runnable supervisor 的原
Controller instance/Prepared upload FD、HTTP/Mission/Node 内容路由、完整 readiness、
新 leader/catchup/Snapshot/完整 joint fault 矩阵、旧 production profile/binary 迁移/
回退、完整 recovery/Root retirement/GC 和原业务目标故障组合继续必需。长期 SLO、
跨主机 RTO/RPO 与物理设备连续性仍需各自真实证据。

本轮零新 Controller children、零 embodied Nodes、零设备、零模型、零模拟器。原
Controller 证据可能含原已实际运行且已停止的软件 Node 历史，保持原义与原时间，
不会由复制恢复其当前可用性、使用许可或物理停止。不能据此宣布 SoftwareComplete。

## 独立进程缺字节反例

`network-content-native-02/result.json` 也通过，新增实际进程层逐成员隐藏原
qualified file：仍有完整多数时仅计其实际完整 members；只剩 metadata 多数而完整
bytes 不足时，真实 leader 的 quorum API 拒绝。恢复原同 inode 文件后重新独立
观察，又得到三者完整多数。该轮随后同样完成实际 SIGKILL、新 epoch 多数及原 member
重开；它不是把 passed 布尔或之前的 byte receipt 当成当前可用性。

最终冻结 native probe 为
`5a03a282817f4cd1c2de78e78116d26a416d68ed42eddc2fea792a9d6763a570`；
源、配置、实际 SQL、原交换记录和 native waits 保留在该独立目录。质量工具还验证
实际响应 PID、完整原 source 的 reap，失败时继续收集并实际 reap 所有原进程。

维护 quality Ruff/format/函数文档与 strict mypy **361 files** 通过。全库 Rust、
全库 Rust **1056 passed、2 ignored**；全部 workspace targets/features Clippy、
私有 Rustdoc、fmt 与双语 strict MkDocs 构建、网站 **9 tests** 通过。
最终进程轮次的原 parent inodes 为 `56698625 / 56698630 / 56698635`，四次 native
waits 仍为 -9、0、0、0；前文其它 inode 对应 native-01，分别保留。

最终补封 v0.4 对旧 `Fence` / `AcceptedIdentity` application RPC 的访问。实际 mTLS
旧 child client 现在收到原 child ingress contract 拒绝；core quorum 观察不变成
应用执行许可。`network-content-replica-tests-final.log` 记录了另一个旧往返 transfer
测试未先等待目标追平的失败，原生产 guard 未改；测试在两次原 effect 前分别观察
实际 source/target 当前日志，仍只发原命令，四秒 idle 窗口无额外 probe。之后
`network-content-replica-tests-final-02.log` **99 passed**。原失败保留且不计通过。

最终冻结 `content-network-probe-adr0169-final` SHA-256 为
`b1bb8085e9c41ccc24ff153e2bd2d808b81aab61f8378efbdcd7995d6a89b4a4`；
`network-content-native-final/result.json` 通过，含完整缺 bytes 反例、实际四次等待、
SIGKILL/new epoch 多数和原 inode/完整候选重开。原 native-01/02 与 final 是不同
运行，原配置、schema、预算、inode 和结果分别保留。
