# F4 双 Group Memory 交接与成员生命周期验收

状态：本工作包的软件实现及下述实际进程验收通过。整体 F4、跨主机/HA 内容恢复、
F5–F10 与 HardwareEvidencePending 仍有独立要求；没有运行实验或模型/模拟器。

## 交付合同与权威

[ADR-0108](../../decisions/0108-durable-group-memory-handoff.md) 与
[管理合同](../../../contracts/memory/handoff-v0.1/README.md) 定义完整原 source/target 成员代际、
当前绑定 attempt、策略 revision、稳定 principal/tenant 和不可续期绝对 deadline。
Application 组合已有停止、撤销、preparation/use/import 和 SQLite authority 事务；
Control 保持唯一 Match/Propose/Commit/Bind/Rebind 与资源权威，交接不选择新 Node。

source 的每个原 attempt 必须保留独立 StopConfirmed，全部原 context/provider revoke
必须确认，随后才在同一事件/checkpoint 事务中发布新 scoped child。旧 manifest 的 owner、
bytes、provider、生产 provenance 和 Group scope 不改写，parent lineage 完整持久化。
原目标完成 Prepare ACK、真实 import、原 backend 当前 bytes probe、当前 use grant 后
才能 ready；显式 commit 再核对当前 State、两端原代际和最早期限，提交后才放行 Execute。

普通 Artifact alias 仍不能跨 Group 扩权。CAS 的 parent-reference 例外只认可这笔交接、
实际证书/原 child import，且逐块复核。typed-map 保持完整原 typed source，不能伪装成
generic child。首批接收方完整退役后，后续已接受 Task 使用新的 Control 绑定、成员代际、
preparation、import 和 use 许可；旧 grant、probe 或首次交接预算不能复用。

超时、策略或原成员/attempt 变化保持 manual/ownership。commit 后原 Execute 尚未投递的
失败窗口同样保持 fence，原 commit 回执与完整接收证明不删除。Controller 重启不会重放
交接中的 Execute 或续期，未完成的原准备被 fence；已完成的交接与 Task 历史保持不变。
source 不自动恢复。每次准备、导入/逐块读取与许可均重查进行中的原两端授权，
恢复拒绝遗失原完整 stop/revoke barrier 的快照。只读记录分页/tenant 过滤有字节上限，
Prometheus 提供 pending/manual
聚合数量。共享 wrapper v40 与 codec v27 拒绝实际旧 Controller writer，包括零事件头。

## 实际软件进程

所有配置通过实际 mTLS/API 登记两个软件 Node，运行部署配置中的真实文件 backend 与 CPU
hash workflow。源文件来自仓库实际 bytes；source 的原输入使用另一个不同 digest 的真实
文件，确保独立验证原 context/provider revoke，而非空集合。Node 的 Group 导出从原 journal
活跃 operation context 获取，Controller 检查其实际当前 provider/member/session。

| 配置 | 实际结果 | 证据目录 |
| --- | --- | --- |
| complete | 原 worker Cancelled + 独立软件 safe-state；原 context/provider 撤销确认；child 导入/probe；commit 后目标计算；后续 Task 新代际/新许可；两次独立 verifier 后目标 Mission Completed | `/tmp/rg-handoff-complete-final` |
| expiry | 原绝对期限到期进入 manual，目标计算为零，原 Control reservations 保留 | `/tmp/rg-handoff-expiry-final` |
| policy-change | 当前原策略 revision/权限变化进入 manual，目标计算为零，原 Control reservations 保留 | `/tmp/rg-handoff-policy-final` |
| controller-restart | ready 后重启 Controller，原接收准备被 fence，manual 保留双方资源；原 issue/deadline 不变，目标计算为零 | `/tmp/rg-handoff-restart-final` |
| 正常并行回归 | 第一 Task 已释放时兄弟 Task 仍计算且许可有效；两次独立 verifier 后 Mission Completed；重启无重复工作 | `/tmp/rg-handoff-normal-final` |

complete 有三条真实 CPU job：source 一条取消，consumer 两条完成；并非三条成功任务。
consumer 首次成员 generation 为 2，后续 Task 为 4，两个 Task 有不同原 preparation/import/use；
source 原 Mission 保持安全 hold。独立子进程读取源/实际导入文件并重算 SHA-256 与结果承诺，
不能由 Controller 的 Completed 或 provider 自述代替。两个真实 SQL trigger 分别中断 handoff
接纳和 commit 的事件写入：前者回滚安全 hold/intent，后者保持 ready，均没有目标 Execute。
所有检查拥有并回收自身进程，五份 cleanup 均为 true；没有停止外部设备或无关服务。

软件 stop export 明确声明 `SoftwareProcess`，只有实际 Cancel 被 backend 记录且原 worker
已经退出，才产生原 attempt/generation/command 的 safe-state。Cancel acceptance、线程仍
活跃、自然 Completed/Failed 或 UNKNOWN 都不合成这项证明。它不建立设备停止、制造商
身份、物理接力、硬实时、耦合多 Role 迁移或异构 regrasp。

复现时使用新的私有输出目录，不覆盖本轮原件：

```bash
cargo build --locked -p integration-server -p roboguide-node -p controller-replica
uv run python tools/quality/check_memory_preparation_processes.py \
  --handoff-case complete --output /tmp/rg-handoff-complete-final
uv run python tools/quality/check_memory_preparation_processes.py \
  --handoff-case expiry --output /tmp/rg-handoff-expiry-final
uv run python tools/quality/check_memory_preparation_processes.py \
  --handoff-case policy-change --output /tmp/rg-handoff-policy-final
uv run python tools/quality/check_memory_preparation_processes.py \
  --handoff-case controller-restart --output /tmp/rg-handoff-restart-final
uv run python tools/quality/check_memory_handoff_schemas.py \
  --directory /tmp/rg-handoff-complete-final
uv run python tools/quality/check_memory_release_upgrade.py \
  --directory /tmp/rg-handoff-complete-final \
  --old-controller /tmp/roboguide-pre-memory-handoff-20261019/integration-server
```

result.json 固定实际二进制、解释器、检查器、backend 与配置摘要。Controller SHA-256 为
`ecc03f886f2982a167d36f25dfb7019ee3ec20257492a2b70bb3d5548bf4c4f8`；Node 为
`cc250c141b6de7264597dd5e5d647bfc69cfbb1be039603943abf04bda615357`。
初期 publication/context 缺口的失败目录 a–c 保留；后续 d/e 是较早基本/撤销链路证据，
f 补充原接收方退役链路；最终口径采用上表冻结源码/最终二进制的 final 配置。

## 检查与边界

Rust workspace 852 passed、2 项原有 ignored；workspace Clippy `-D warnings`、fmt、
private Rustdoc 与文档网站同步/strict build 通过；历史绝对路径链接提示保持原记录。
Memory provider Python 测试 33 passed；相关 Ruff、strict mypy、函数文档检查通过。
本地 jsonschema 4.26.0 验证实际两端配置/计划、handoff、原 preparation/control/use 回执，
并拒绝十类结构变体。实际旧 v39 Controller 在非空历史由 codec v27 拒绝，在零 Node/零事件
头由 wrapper v40 拒绝；所有 authority table 逻辑摘要前后相同。Node 协议/journal 不新增
隐含交接权限，原 v0.10/v11 current-use、原查询与停止合同保持各自门禁。

本 profile 验证同机、两软件 Node 的内容与操作系统工作线程事实。真实设备、跨独立主机、
active-map 物理定位、HA snapshot/archive/CAS 恢复、
完整 GC/数据面生命周期及后续 F5–F10 保持独立证据和验收；不报告整体 SoftwareComplete。
多 provider Unknown 的原只读恢复已由 ADR-0107 独立验收，不由本 profile 重复证明。
