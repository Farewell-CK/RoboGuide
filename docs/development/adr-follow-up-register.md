# ADR 遗留项核验与后续实施

本清单以 `dev@206dc08` 为核验起点。2026-10-12 已扫描全部 78 个既有 ADR；32 个文件含
候选遗留语句，其中包含正常 pending commitment 等流程描述，不能把关键词数量当作
缺陷数量。状态按当前代码、后续 ADR 和测试证据确认；历史 Decision 保留，完成边界在
原 ADR 追加或修正。软件实现、确定性检查、实际进程验证与物理证据分别记录。

## 已实现而需要纠正文档的项

| 原 ADR / 遗留 | 当前实现与证据 | 同步状态 |
| --- | --- | --- |
| 0020：RT-G3 attempt history | ADR-0028；Runtime logical slot、durable generation、attempt history；relation rebind 和 current-attempt outcome fences | 原 ADR 已追加软件闭环说明；RT-G9 保留 |
| 0028：multi-role joint recovery | ADR-0061；仅独立 Session 的 whole-Group retained-binding continuation；实际 scheduling/HTTP/timer/checkpoint 与 transport/isolation 回归 | 原 ADR 已注明已实现的子集；coupled/migration 与多 Actor 物理续跑保留 |
| 0033：verifier identity / negative verdict | ADR-0039/0048；task_verifier、verifier_source、runtime_replay 的 positive/negative/stale-attempt/restore 检查 | 原 ADR 已修正；multi-source truth 与通用 State basis 保留 |
| 0036：generic verifier ingress | ADR-0048；部署 final-verdict source codec、admission 和 timer 消费者 | 原 ADR 已修正为该 profile 已实现；streaming/remote authentication 保留 |
| 0048：仍标 Proposed 的实现状态 | 上述真实 source/consumer、timer 和正负 verdict 回归已存在 | 改为 configured final-verdict profile 已实现；来源与物理边界不扩大 |
| 0039：同一 verifier ingress 遗留 | 原文已引用 ADR-0048 并区分来源与 freshness | 文档已有演进说明；配置重启后的完整 MI policy history 仍待实现 |
| 0066/0067：Proposed 状态与软件实现不一致 | Controller loopback assessment、MI durable hold/reconsideration 和 interruption 回归已存在 | 标注 default-off 软件已实现；物理效果与 semantic fidelity 未证明 |
| 0070：Control takeover 状态 | scoped authorization、Match/Schedule/Commit/Rebind 与 distinct-entity standby 回归已存在 | 标注 Control 已实现；应用 command 和真实 standby stop gate 保留 |

## 当前软件工作与依赖

用户授权推进故障隔离、事务性能和架构演进，并明确公平调度与控制面复制按依赖逐步
完成。实施顺序如下；各阶段先记录 V2/ADR，再实现、验证并提交。E6 持续暂停。

| 工作 | 当前状态 | 验收要求 |
| --- | --- | --- |
| S1：运行期 typed contract isolation | 软件实现与生产 application path 回归通过；ADR-0079 | 5 项生产 timer/HTTP/SQL 检查及 Runtime immutable-time 检查通过；全 workspace、Clippy 验证；物理时限与 stop 未证明 |
| S2：有界事实批处理 | ADR-0080 已实现；6 项真实 gRPC、嵌套 savepoint 与 128/128 合成进程检查通过 | 1/N 轨迹等价、拒绝/SQL/hold/barrier 均验证；2ms 窗口降低 serialization/Commit 次数，保留单条兼容模式 |
| S3：增量 checkpoint 存储 | ADR-0081 软件已实现、显式 opt-in；默认 full | 8 项存储/损坏/rollback 检查、真实应用恢复与进程复核通过；写入 bytes 降低，吞吐收益尚未建立；首次 startup timeout 保留 |
| S4：多 Mission 优先级/公平/准入 | 待 S1–S3；Orchestration logical dispatch policy + Control commitment | priority/aging 持久；饥饿与资源冲突回归；只选择 Ready logical work；固定 Actor/Context/未来窗口不被策略绕过 |
| S5：控制面复制与切换 | 待稳定事务/恢复/调度 authority | replicated durable acceptance、single leader delivery、term/session fence、失去 quorum fail closed；leader crash/partition 不重放物理歧义、不释放未停止 ownership |
| S6：受支持的抢占 | 依赖 S4/S5 和明确 stop/repeat contract | 原 physical stop proof、隔离 stop support、原 retry budget；运行工作未提供该声明时仍排队；软件证据不能替代硬件验证 |

## 继续保留的明确边界

| ADR / 工作族 | 遗留项 | 当前处理 |
| --- | --- | --- |
| 0012、0016、0023 | event-complete reducers、replication/HA、catalog recovery、durable acceptance | S2/S3/S5 联合关闭相应软件切片；不会按每 N 事件省略未可恢复事实 |
| 0019、0020、0028 | 真实 readiness 故障注入、Robonix quality/frame mapping、relation actuation/硬实时停止 | 保留物理/部署 gate；不会用 offline fixture 宣称关闭 |
| 0025、0037、0040 | distributed ExecutionGroup authorization、Memory selective-import command、scope/visibility、structured entity/parameter contracts | 独立 authority/协议切片；既有 discovery 和单 Node invocation 不是分布式授权 |
| 0029、0041 | 多 Mission priority/fairness、准入、preemption、动态预测及更大优化器 | S4/S6 先处理有边界的软件机制；成本模型/CP-SAT/物理 travel evidence 单列 |
| 0031、0032、0037、0039、0054 | Catalog negotiation、丰富 channel、semantic grounding、policy history、execution-time replanning | 不把 planning/admission reconsideration 当成执行期 plan revision；来源/配置 history 仍需独立实现 |
| 0057、0059、0060、0061 | 自动恢复策略、coupled/migrating recovery、真实多 Actor continuation | 显式 Role/独立 Group 子集已有；新策略保留原 stop-proof/Actor/预算规则 |
| 0066、0067 | bounded feedback 的物理效果、MI semantic fidelity、execution-time replanning | opt-in pre-admission 软件已实现；真实因果/物理效果保持未证明 |
| 0068、0069、0073、0074、0078 | navigation/relocation physical release、三 agent Task4 native mapping、正式人口/公平证据 | 当前 software/profile 不等于新 population 发布；E6 和物理实验继续暂停 |
| 0070 | application takeover command 与真实 standby 证据 | Control typed authorization/registry/commit/rebind 软件与回归已核实；应用 command 尚无，现场 replay 与物理 stop 验证保留 |
| 0075、0076 | diagnostics/连续活性运行证据与 TTL 不变 | 当前离线/本地进程测试已有；历史 pilot 因果和 hardware stop 没有升级为已证明 |
| 0077 | 真正运行期局部分类与持久 isolation | ADR-0079 完成声明 deadline 的 exact-Task 子集；原注入回归仍只证明机制，不扩大未知错误分类 |

Normal pending reservations、未来 scheduling intervals、未到达的 source evidence 是可接受的
运行状态，不是自动需要删除的遗留。多 Group decomposition、完整世界 Belief、融合、空间
交通图与模型策略属于后续能力选择，只有实现和证据明确时才更新为完成。

历史原始审计与核验产物位于仓库外的审计目录，真实结果不纳入 Git。本文件会随各软件
切片完成更新；原 ADR 的物理限制和未证明项也会同步保留。
