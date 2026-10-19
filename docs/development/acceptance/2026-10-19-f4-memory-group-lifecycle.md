# F4 多 Node Memory 生命周期与执行前门禁

状态：本次限定的软件切片已验证，F4/整体 SoftwareComplete 仍未关闭。设备、模型、模拟器
均为 0；没有运行实验。实际工作为原仓库文件的 CAS 交换、CPU 哈希与独立复算。

## 修复内容与权威

`b6a4a9e` 将 Memory 检查接入统一 `safety_stop::blocks_release` 后，会把一个 Task 的本地
completion 也阻塞在整个 Mission 的 Memory 许可上。若兄弟 Task 正在工作，可能卡住 DAG。
本次保留物理 stop 与内容使用的独立权威，并按实际释放范围处理：

| 路径 | 当前行为 |
| --- | --- |
| 本地 completion | 可记录 `AwaitingSatisfaction`；不释放资源、不代替语义满足 |
| 正常 Task 满足 | 等待该 Task 所有原 context/provider 的 revoke ACK；活跃兄弟 Task 保持许可 |
| 整组取消、最终执行失败、最终负 verifier | 先持久化全组原 context/use fence，再等待全部原参与方；需要的 stop 证明另行核对 |
| 停止后独立 Role 恢复 | 释放前检查原 attempt 的 context/provider，不要求无关 peer 放弃 ownership |
| 整组 continuation / Mission resume | 全组 context/provider 检查与既有 stop/hold/recovery gate 同时成立 |
| 执行前发送 | `MemoryDispatchPermit` 取 preparation、availability、当前 grant 的最早原到期时间；每次发送重查所有 pending Group inputs，包含本身没有 Memory 要求的 peer |

Memory Node context 的 revoke ACK 与 provider current-use revoke ACK 是两组证据。缺少
任一组、缺 provider、重复 provider、Unknown 或替换 backend instance/contract 均不放行。
revoke 的 `import_ids` 为空，Controller 仍从原 preparation 恢复每个 provider 的完整输入
集合，核对原 availability 证据，不能用空列表跳过原 backend 检查。

Control 保持唯一资源/绑定权威；Runtime 保留原 command/attempt；Local EAIOS 保留实际操作
和安全权威。这里没有增加设备名、Node ID、固定节点数或任务图分支。测试中两 Node 是实际
已配置、经证书登记的软件进程；多 provider 的负向用例是明确标记的离线元数据 adversary，
不冒充实际多 provider 效果或设备证据。软件许可撤销不是物理停止证书。

## 版本与恢复

Controller 与 replica supervisor 的共享 wrapper 为 `roboguide.controller-checkpoint/v38`。
Node Contract v0.10、config v0.11、journal v10 和原 preparation/use wire schema 保持原合同。
standalone v37 checkpoint 可经过完整原证据校验后保守恢复；未结束 use 先撤销，原 issue
time、receive time、import identity、次数和期限不刷新。v36 及更旧 wrapper 不能携带当前
use authority。replicated profile 的旧 schema/policy 仍要求独立显式迁移，不通过清空集群升级。

实际旧 v37 binary 对已有 Group 历史和零 Node/零事件 v38 checkpoint 都必须拒绝，且所有
authority table 的逻辑行摘要保持一致。它与当前 Controller/supervisor 的只读 schema 输出
单独核验；不拿同进程构造的旧版本标签替代旧二进制。

## 复现与实际产物

```bash
cargo build --locked -p integration-server -p roboguide-node -p controller-replica
uv run python tools/quality/check_memory_preparation_processes.py \
  --group-lifecycle --output /tmp/rg-memory-group-20261019-c
uv run python tools/quality/check_memory_preparation_processes.py \
  --output /tmp/rg-memory-preparation-20261019-final
uv run python tools/quality/check_memory_release_upgrade.py \
  --directory /tmp/rg-memory-group-20261019-c \
  --old-controller /tmp/roboguide-controller-v37-before-release-scope-20261019
```

目录参数必须是新的私有目录；不能覆盖历史产物。归档旧 binary 只在仓库外用于拒绝检查。
Controller binary SHA-256 为
`9cf9b7bb742e1adea384b70741a5ad065b5c55754c40d3e4c3376de0ea20dbcb`；Node binary 为
`943ec1d82ca1ddcb6eda56a3ba490b83198d6543192719e9809b014c724e0bbd`。
每份 result 都保留实际源码/解释器/配置 identity，SQL、原文件、独立验证和日志保留在目录内。

`rg-memory-group-20261019-c/result.json` 通过两软件 Node、实际 mTLS、实际 CAS 导入、两个
CPU job 和两个独立 verifier。先提交的 verifier 回执可以 `applied=false`，随后原证据由
应用 timer 归约；不重新 POST 或重算。`group-partial-progress.json` 观察到 Task 0 为
Completed、Task 1 为 Active/Running，后者 use 未被撤销；最终两个 context 和全部原
provider 都确认撤销，Mission 完成。Controller 重启后原预算不变，每个后端仍只有一个 job。
`owned-process-exits.json` 确认本次进程全部 reap。

标准大小实际链路 `rg-memory-preparation-20261019-final/result.json` 也通过：保留真正的 import
completion SQL 故障、原 provider 只读对账、2 个 Node/2 个 backend 重启、2 个独立复算，
原 receiver budget 未改变。检查器通过 GET 等待原 verifier receipt 的 applied 状态，接纳回执
不再被误当成已经完成释放。

失败原件 `rg-memory-group-20261019-a/failure.json` 保留为未通过：当时声明 30 万轮 CPU
工作，超过检查器 240 秒外层等待。它已经观察到独立 Task 推进，但没有整轮完成证据。
后续 b/c 使用现有持续使用检查采用的 5 万轮工作量，算法、原 120 秒 preparation 窗口和
显式 300 秒计算上限不变。a 不计作通过，也不与 b/c 作为同一工作量或性能结果汇总。

## 软件检查及剩余要求

完整 Rust 工作区 840 项通过、2 项既有 ignored；其中 Integration Server 226 项、Orchestration
73 项。新增生产路径回归覆盖正常独立推进、双 Node 取消、缺失/重复/替换 provider 拒绝、
旧 wrapper 恢复与原时间保留，以及 Memory 未就绪时整组普通 peer 不抢先发送、其它 Group
仍处理自己的 outbox。Clippy、private Rustdoc、fmt、Python 变更文件 Ruff/strict mypy/函数
文档检查通过。没有把离线合成 ACK 当作进程正向证据。

最终质量日志分别为 `/tmp/rg-memory-group-workspace-final-20261019.log`、
`/tmp/rg-memory-group-clippy-final2-20261019.log`、`/tmp/rg-memory-group-rustdoc-20261019.log`；
文档同步与严格站点构建通过，见 `/tmp/rg-memory-group-docs-build-20261019.log`。
旧 v37 实际拒绝记录见 `/tmp/rg-memory-release-upgrade-final-20261019.log` 和 c 目录内
`release-upgrade-*/result.json`，两个数据库所有 authority 行的前后摘要均相同。

多 provider 的部分 Unknown 结果对账、跨独立主机网络故障、完整成员变更/handoff、HA 内容
恢复、F5–F10 全部剩余软件要求继续实施。已有 active-map 软件证据另见
[2026-10-16 记录](2026-10-16-f4-active-map.md)。物理停止/定位、硬实时和硬件连续性仍为
HardwareEvidencePending。本记录不关闭完整 F4、E6、实验或发版 gate。
