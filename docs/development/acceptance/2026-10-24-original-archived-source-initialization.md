# 原归档源首次初始化与恢复验收

状态：原初始化、追赶、实际入口、同 bundle 反例修复与最新完整存量的三 leader 冷恢复
均通过；远程 CI 另行核对。以下记录不关闭全局 Archive/引用覆盖或全部非发布整改。

本包对应唯一实施队列 A1/N1/N3，设计为
[ADR-0195](../../decisions/0195-original-archived-source-initialization.md)，部署入口见
[初始化说明](../original-source-initialization-v0.1.md)。代码在同一
`codex/nonrelease-remediation-closure` 分支开发，原已推送基线为 `faede53c`。
实验、设备、外部模型、模拟器和正式发布均未运行。

## 原来源与不可替代的证据

证据根为 `/data/workspace/roboguide-evidence/nonrelease-closure/p7/`。原来源
`recovery-profile-archive-source-retained-01` 是先前实际文件服务进程门禁留下的已排空
Controller SQL/CAS，并有全部 owned process 的实际 wait。它含原软件 Node 历史；本包
新启动的是基础设施副本和真实 Controller 进程，不能把来源历史说成不存在。

原 archived floor 为 82、checkpoint sequence 为 102、热尾为 20 条，保留 77 个原命令。
完整来源 digest 为
`sha256:2227ab584140bf6f91002ee905574332684c271d17a3ad62c7e3cc1231f260a6`，
原 CAS owner 为
`artifact-store:be2a1e2d6f97f3ddc0047addf1370607c1656646b23f2527d4cb1c47e6ed4ac9`。
没有补造 floor 之前的事件、receiver predecessor、来源 owner 或原命令结果。

## 已保留的真实失败

- `historical-log-processes-native-01` 拒绝缺少首个归档 event/owner 绑定的候选。
- `archived-initialization-native-01` 暴露新 parent9 空 trust bootstrap 的 mandatory
  metadata 计数遗漏；后续实际初始化和原 SQL 回归验证修复。
- `archived-initialization-runnable-01` 保留原 standalone checkpoint/profile 拒绝；
  当前通用恢复仍拒绝 profile mismatch，仅实际当前 child 的初始 quorum proof 可绑定。
- `archived-initialization-runnable-02` 至 `04` 分别保留缺原 Node TLS、Artifact 证书
  profile，以及 Operator TLS 与开发管理员冲突的拒绝。部署声明补齐原输入，没有降级身份。
- `archived-initialization-runnable-05` 至 `07` 保留实际异步 TLS 完整回复未 flush 的 EOF。
  维护入口在完整 frame/stream boundary flush；原 intent/Commit/ACK 顺序保持。
- `archived-initialization-runnable-08` 至 `10` 保留完整恢复耗尽原整体预算的 Unknown。
  `10` 的新上传已完成但原恢复结果未提交，不能倒填成功。完整历史新门禁在首次操作前
  声明 300000 ms；普通原门禁仍使用 120000 ms，原失败和原预算均不改写。
- `archived-initialization-runnable-11` 的首次接管实际 132.7 秒成功，同 ID 返回原完成。
  随后旧检查器用上限加一构造冲突，实际得到合法的 400；修正为范围内的不同预算后
  新建完整门禁，原失败和所有实际 supervisor wait 0 继续保留。
- `archived-initialization-recovery-native-01` 拒绝缺少原 Source CAS/Archive 目录的清单。
  后续显式声明实际原数据，并只复用同一实际 Replica profile 的精确重复 Archive 投影校验。
  `recovery-native-02` 已完成备份/verify；其缺 head 反例只重算文件哈希、遗漏 SQLite 整体
  identity，因而先被完整 identity 校验拒绝。新反例同时重算这些证据，再检查 mandatory head。
- `archived-initialization-recovery-native-03` 至 `05` 保留第三个目标在读取时丢失 epoch
  的失败，所有 supervisor 实际 wait 0。`04`/`05` 的有界只读 GET 明确记录连接关闭和后续
  拒绝，未把重试当作修复。后续将候选/读取接纳和 supervisor 观察移至原持有工作任务；
  Raft log/range/vote/committed 读取改用原 leased inode 的只读 WAL 事务。独立实际 SQL
  回归在应用 writer 持有不同未提交值时 0.19 秒完成，只返回原已提交 vote/commit/log。

实际只读 Root 采样见 `archived-root-inspection-cost-01.*`、
`archived-root-validation-profile-01.*` 和 `archived-root-inspection-comparison-01.json`。
SQL statement preparation 复用只缓存语句，不缓存行或授权；每次仍读取实际事务数据。
原 witness 的自身写入、外部提交、DDL、generation 与 rollback 拒绝回归继续通过。
同 epoch/成员的紧邻原输入可减少重复复制；所有候选字节和最终完整 quorum 仍重新检查。
这些短采样不代表长期吞吐或 SLO。

## 专项与最终组合门禁

`archived-initialization-native-02`、`archived-initialization-interrupted-native-01`、
`archived-initialization-log-native-01` 和 `archived-initialization-snapshot-native-01`
保留各自实际二进制 identity，覆盖旧 voter/旧 writer 拒绝、源 durable intent/capture、
原进程 SIGKILL/wait、同 ID Unknown 只读，以及缺失接收方经 retained Log 或 purged
Snapshot 追赶。它们早于后续传输 flush/语句准备复用；不得冒充相同最终二进制证据。

最终冻结二进制记录在 `initialization-frozen-binaries-16.json`；前一轮成功入口的原二进制
继续保留在 `initialization-frozen-binaries-12.json`：

| 实际二进制 | SHA-256 |
| --- | --- |
| `controller-replica-adr0195-native-16` | `51692aa03051cf44e54c0b19da27e77d27ec78dcd49d2df0f967bfbeed7e30ca` |
| `integration-server-adr0195-native-06` | `4772c11925eee63a3d64d491145f0fda376b9a19402292c2832559a62ef5fe7d` |
| `content-network-probe-adr0195-native-07` | `c14a5399447dae0d48739da4309b09edc30ffc250cbb08c6582f27ac42b0b461` |
| `content-stream-probe-adr0195-native-03` | `fa4ad2c42f47e6f65032e947ae5f8caf47ac10b3fad8319385248f9c5540ade7` |

`archived-initialization-runnable-12` 已用其原二进制通过完整入口链；最终批次的
`archived-initialization-runnable-13` 已通过实际原身份/TLS 部署、普通
上传/完整字节 quorum、真实 child SIGKILL/wait/同 epoch 重启、原 owner 停止后的
内容读取、Prepared 接管及失败后的原 Unknown/只读对账/精确 release。
后者实际首次接管 140.97 秒、SQL 故障路径 158.11 秒；性能缺口与短观察限制继续保留，
不能把 300000 ms 显式预算内通过解释为长期 SLO 达标。三个实际 supervisor 均 wait 0。
最终批次 `archived-initialization-interrupted-native-02`、`archived-initialization-log-native-02`
和 `archived-initialization-snapshot-native-04` 均通过。

`archived-initialization-recovery-native-06` 使用与原失败相同的完整 bundle：
`sha256:ea7265b16d865aa9bd6bbf2e8ce0140ce45cebd15745c11abbc26ac2930ceb4d`。
实际 source SQL sibling/inode 和 CAS 租约在整个重启/换主/读取期间被独立持有，实际
inspect 被拒绝；恢复后的服务因此不能重新读取或重做原来源。
三个真实 leader 按 337 → 307 → 311 完成内容和原 Unknown assessment 的全部六次 GET，
每次均为一次 200，没有连接重试；三个 supervisor 最终均实际 wait 0。
原初始化 policy/head/operation 的原始字节与 anchor 保持，原 head 行数分别为 1/0/0。
独立核对见该目录的 `independent-verification.json`：再次直接读 SQL，并哈希三个实际
Root 的所有当前必需文件，均通过。冷恢复保留 bundle 中声明的原 child binary；它不会
把一次恢复偷换成二进制/共同策略升级。新 child binary 的完整入口由上述最终门禁另验。

完整 bundle 还通过实际旧 profile 拒绝、原 inode 活跃锁拒绝、私有 payload 和重新计算
文件/SQLite/manifest identity 的缺 mandatory inventory 反例；restore 重复命令只读。
后续 A2/A3 与 B–F 的原完成条件保持。

最终同批次的新入口存量也由 `archived-initialization-recovery-native-07` 完整 backup、
verify、restore 和重启验收通过，manifest 为
`sha256:938f4822f0d3f10b42db222fd61582789c9f5ab418213f6e2e65ba396d909801`。
三个实际 leader 为 311 → 307 → 337，六个 GET 各一次 200，三个 supervisor 均 wait 0。
该目录的独立核对再次确认原 1/0/0 初始化行、policy/head、anchor 与全部当前必需 CAS
字节一致；原来源继续被实际租约锁住，两个经过完整重算的缺 inventory/私有文件反例均拒绝。

相关本地检查已有 Replica 152 项、supervisor 12 项、Artifact 36 项、原 State 初始化合同
1 项、恢复工具 193 项及 Memory provider 回归通过，全工作区全 target/feature
Clippy 与包含私有项的 Rustdoc 均以 exit 0 完成。Python Ruff、strict mypy 和函数文档
检查通过；严格文档预览检查 779 个 HTML 页面/46 条维护路由，历史未解析 anchor 为 0。
最终原进程组合已按上述固定 identity 验收，CI 状态单独确认。

## 完成边界

初始化 anchor 是原来源历史，不能赋予新 use grant 或永久 pin 已退休内容。恢复保留
member-local 原 policy/head/operation、Unknown 与首次 budget；Raft Snapshot 不替换它们。
新 roots、TLS、实际 OS wait 和完整副本内容均不建立设备停止、硬实时、跨主机数据面、
物理连续性或总体 SoftwareComplete。A2–F 与原全部非发布完成条件继续必需。
