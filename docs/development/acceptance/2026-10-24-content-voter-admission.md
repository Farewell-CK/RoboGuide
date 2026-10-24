# 完整内容 voter 准入与 Snapshot 活性验收

对应 [ADR-0192](../../decisions/0192-complete-content-voter-admission.md) 和
[ADR-0193](../../decisions/0193-snapshot-preparation-raft-heartbeats.md)。范围是单机真实
Raft/mTLS/SQL/CAS 进程；不创建 embodied Node、设备、模型或模拟器，不宣告总体完成。

## 发现与修复

实际损坏 learner 文件的反例中，原调用返回错误，但 voters 从 `{53,71}` 变成了 joint
union `{53,71,89}`。错误返回不等于配置回滚。现在进入 joint 前先证明原完整 quorum、
每个新 voter 的精确 Root/metadata/字节和新集合多数派，并复核原 epoch、应用和 trust。
实际 learner 已入组且绑定相同时不重复写 enrollment。显式空应用和 metadata-only 的
原语义保留。内容 Commit 与切换共用独立持有的串行门禁；调用方超时/取消后，已排入
Raft 的原操作仍持有它，直到实际结果返回。没有续期、重放效果或额外分布式权限。
另一个实际 mTLS 反例证明，旧 epoch 请求原先会在报错前完成 enrollment/joint。现已将
调用者原 epoch 传入实际准入，并保留实际 member writer 与捕获的原 TLS 连接；排队后和
效果前复核同一 epoch、成员和当前 trust 下原 chain/leaf 的有效性。取消网络等待不释放
实际未决 writer，新证书不为旧请求补权限。

Snapshot 回归还暴露 source term 2、返回 voter Candidate term 6 的准备期选举，以及
Root7 接收方已安装、来源却因默认 200ms metadata RPC 超时仍报告 learner matched=None。
显式 Snapshot 内容路径现在从首次准备到 metadata 投递都发送普通原 vote/原 committed
cut 的空 Append；prefix conflict 不算追平，真实 higher vote 交回 OpenRaft。Root6/7 的
单 chunk 等待至多 10 秒，仍受首次固定的全程 15 秒源预算约束。原接收预算/Unknown、
实际 blocking worker、Root 与原引用生命周期保留。私有 probe 可显式启用有界上游
warning 字段，帮助区分接收完成与来源实际收到 ACK；它不赋予任何权限。

## 实际证据

证据根 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`：

| 证据 | 结果与边界 |
| --- | --- |
| `membership-content-admission-counter-02.log` | 修复前真实 joint 扩大反例，失败记录保留 |
| `membership-replica-tests-final-01.log` | 140 通过、1 个新门禁测试误用最小接收预算而失败；修正测试预算，未放宽产品校验 |
| `membership-replica-tests-final-02.log` | 140 通过、真实 Snapshot 准备期选举反例失败，原记录保留 |
| `membership-native-final-01` 至 `04` | 真实 Snapshot 已安装但 learner 后续 metadata 未追上；04 的实际上游 warning 定位 200ms metadata 超时，全部实际进程 wait=0 |
| `membership-native-final-05` | Root7、原两 voter、真实 Snapshot/learner；损坏拒绝且原 SQL/Log/voters 不变；修复后实际 old/new joint；旧 voter 停止后新完整 quorum；全部 wait=0 |
| `membership-replica-tests-final-05.log` | 142 项通过，含实际取消/到期后持有门禁、队列不产生效果、实际 higher vote 返回且不产生 Snapshot receive 意图 |
| `membership-stale-epoch-counter-02.log` | 修复前实际 mTLS 请求返回 epoch 错误，却分别把成员配置推进到 index 2/4；原反例保留 |
| `membership-replica-tests-final-07.log` | 143 项通过，含旧 epoch 拒绝、实际 member writer 随独立任务保留和原证书排队/失效回归 |
| `membership-legacy-tls-counter-01.log` | 开发中新增权限检查误要求旧版具备轮换账本；新增真实静态 mTLS 回归发现并修正兼容分支，未为旧版补造新 authority |
| `membership-replica-tests-final-08.log` | 最新 144 项通过，含旧版静态 mTLS 管理正常工作且 SQL 仍无轮换账本 |
| `membership-admin-native-final-01` | frozen final-07，经产品现有 `ReplicaAdminClient` 和真实 mTLS 管理入口完成 learner、损坏拒绝/无新 Log、修复后 old/new joint 与旧 voter 停止后完整新 quorum；全部实际进程 wait=0 |
| `membership-admin-native-final-02` | 最新 frozen final-08 的同一产品 mTLS 管理门禁通过；network profile 的调用者成员资格与 trust 来自同一 committed SQL 读取，全部实际进程 wait=0 |
| `snapshot-heartbeat-native-final-01` | 内核 SIGIO=29，真实源 open 阻塞 3.533 秒；35 次独立 SQL vote 采样始终 term 2/leader 443；后续原字节/Snapshot/换主/同 inode 重开通过；5 个实际 wait 为 `-9,0,0,0,0`，-9 是明确的原 SIGKILL |
| `membership-supervisor-tests-final-01.log` | 11 项通过 |
| `membership-runnable-native-final-01` | 原实际 Controller/HTTP/CAS/child restart、跨 owner 读取、Prepared recovery 原 Unknown、原放弃和 Completed retirement 组合通过；3 个 supervisor wait=0，使用该 case 冻结的 final-05 supervisor |
| `membership-clippy-final-07.log`、`membership-rustdoc-final-03.log` | 全工作区所有 target/feature Clippy 与受影响私有 Rust 文档通过 |

最新 frozen binaries 为 `*-adr0192-0193-final-08`，全部由 exit=0 的 locked build 产生：

| 二进制 | SHA-256 |
| --- | --- |
| content-network-probe | `99b94522089124e2384351feb06ba2ecb45cdd3b031dcdbf3e9f4f0ccd858a62` |
| content-stream-probe | `a3142b4eee7a309ea6b53aadd833bf3c6f6ef499c30a78c21ad49959d4890ec0` |
| controller-replica | `2754b5f8ede1c3cf4d38990d3386f9076df8b866abb0c6ae749294bc6eed7077` |

原 Controller binary 为保留的 `integration-server-adr0190-final-01`，SHA-256
`b52ff24226969eeac1f453b0e99792ffc5b3e1f3c77b2d9f1b124dc133a81039`。
冻结清单另保留 Cargo.lock/package 声明摘要。进程门禁运行中未替换其源码或二进制。

维护命令仍是 `check_network_content_processes.py --learner-joint` 与
`--snapshot-catchup`；各 case 独立固定自己的源/二进制，不把较早构建冒充较晚构建。

远端 `14ccdad` 的 Python、Rust 测试/Clippy/私有文档与双语文档工作流通过；CI 的原
Artifact SQL 恢复门禁在第五个 case 启动时报 `AddrInUse`，原日志与 artifact 保留于
`membership-ci-14cc-failed.log` 和 `ci-14cc-native-failure/`。启动检查此前逐次申请并释放
三个端口，允许同一进程重复选中端口；现改为同时保留整个端口集合后再启动，不自动重试
失败的 Controller。该改动消除集合内重复，不能从端口占用日志反推唯一历史起因。
`ci14cc-ports-native-01/` 使用上述原 frozen Controller 完成全部 10 种实际恢复 case，
进程退出为 0；checker 的 Ruff、格式与 strict mypy 通过。原远端失败没有被覆盖。

这不关闭完整 A1/A2：原归档源、全 producer/partial staging、故障组合与滚动升级/回退
继续按唯一整改队列完成。跨主机故障域、设备停止/物理连续性和长期 SLO 仍无本项证明。
