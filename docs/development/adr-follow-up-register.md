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
| S4：多 Mission 优先级/公平/准入 | ADR-0082 已实现；5 个 Core + 7 个 app 检查通过 | logical aging、实际资源 gang 饥饿反例、round/32-turn bound、restore、due window、priority HTTP 与 admission 429；不授予物理时限/停止保证 |
| S5：控制面复制与切换 | ADR-0083 的静态三成员 trusted-loopback 软件已实现；ambiguous Commit response 会在同 epoch 下回读 accepted identity，匹配则继续本地 Commit，否则回滚并交给 supervisor 重建 | upstream storage conformance、真实 engine/process quorum/leader loss/rejoin、Unknown/ownership/policy/journal 与旧 route fence；Artifact/Memory 内容及跨主机/物理发布保留 |
| S6：受支持的抢占 | ADR-0084 的显式 future planning 子集已实现；运行中物理抢占仍未实现 | generation/更高 priority、unbound/未 Commit/strictly future、waiting age/restore、HTTP/SQL rollback；运行工作继续保留 stop/repeat/handoff/现场 gate |
| S7：Node 侧 epoch fence 与 transport hardening | ADR-0085；复制 profile 的 Execute/Cancel 携带可选 epoch，Node journal 持久化最高 epoch；重复 Node 注册不再驱逐 live route；全拒绝 batch 也 fence rejected sessions；HTTP listener 异常进入 fatal 通道；Node-authored execution fact 的 owner/terminal rejection 使用 typed `FactRejected`，未分类 Runtime/Control/SQL 错误保持 fatal | 防止已观察新 epoch 后的旧 Controller 命令；不提供物理停止、mTLS、跨主机认证或竞态窗口的物理证明；不把全量 Mission-local isolation 宣称完成 |
| S8：增量身份安全档案 | ADR-0086；Node gRPC 可选双向 TLS、证书 digest 到 NodeId 绑定；replica RPC 可选 envelope secret；loopback 兼容模式保留 | 离线身份/digest/配置检查通过；跨主机证书轮换、真实 PKI 运维和物理身份仍未证明 |
| S9：复制历史锚点压缩 | ADR-0087；完整 checkpoint 加 `HistoryAnchor` 与连续 retained tail，支持空 tail 重启和快照 materialization | 压缩/恢复/OpenRaft conformance 通过；外部完整归档、长期容量曲线和生产运维策略仍待独立验证 |
| S10：Mission Hold SLA 与有界 ingress | ADR-0088；Hold 保存 review deadline/escalation，validated reader ingress 为每 session 有界并产生 transport backpressure | 离线 Hold/背压/故障路径通过；不自动释放资源，不证明物理停止或全量运行期错误分类 |

## 继续保留的明确边界

| ADR / 工作族 | 遗留项 | 当前处理 |
| --- | --- | --- |
| 0012、0016、0023 | event-complete reducers、replication/HA、catalog recovery、durable acceptance | S2/S3/S5 已关闭 batch/delta/静态 quorum metadata 软件切片，并同步原 ADR；完整 reducers、跨主机与内容可用性保留 |
| 0019、0020、0028 | 真实 readiness 故障注入、Robonix quality/frame mapping、relation actuation/硬实时停止 | 保留物理/部署 gate；不会用 offline fixture 宣称关闭 |
| 0025、0037、0040 | distributed ExecutionGroup authorization、Memory selective-import command、scope/visibility、structured entity/parameter contracts | 独立 authority/协议切片；既有 discovery 和单 Node invocation 不是分布式授权 |
| 0029、0041 | 多 Mission priority/fairness、准入、preemption、动态预测及更大优化器 | S4 priority/fairness/admission 与 S6 future planning 已实现并同步原 ADR；运行中物理抢占、成本模型/CP-SAT/travel evidence 保留 |
| 0031、0032、0037、0039、0054 | Catalog negotiation、丰富 channel、semantic grounding、policy history、execution-time replanning | 不把 planning/admission reconsideration 当成执行期 plan revision；来源/配置 history 仍需独立实现 |
| 0057、0059、0060、0061 | 自动恢复策略、coupled/migrating recovery、真实多 Actor continuation | 显式 Role/独立 Group 子集已有；新策略保留原 stop-proof/Actor/预算规则 |
| 0066、0067 | bounded feedback 的物理效果、MI semantic fidelity、execution-time replanning | opt-in pre-admission 软件已实现；真实因果/物理效果保持未证明 |
| 0068、0069、0073、0074、0078 | navigation/relocation physical release、三 agent Task4 native mapping、正式人口/公平证据 | 当前 software/profile 不等于新 population 发布；E6 和物理实验继续暂停 |
| 0070 | application takeover command 与真实 standby 证据 | Control typed authorization/registry/commit/rebind 软件与回归已核实；应用 command 尚无，现场 replay 与物理 stop 验证保留 |
| 0075、0076 | diagnostics/连续活性运行证据与 TTL 不变 | 当前离线/本地进程测试已有；历史 pilot 因果和 hardware stop 没有升级为已证明 |
| 0077 | 真正运行期局部分类与持久 isolation | ADR-0079 完成声明 deadline 的 exact-Task 子集；原注入回归仍只证明机制，不扩大未知错误分类 |
| dev-review §1.4、§2.3、§3.2、§4 | 外部完整历史归档、EASY/backfill、终态 Mission GC、空间资源图与租户配额 | S8–S10 关闭了安全档案、checkpoint 锚点压缩和 validated ingress/hold SLA 的软件切片；完整归档、自动 GC、空间调度和长期公平性仍保留 |

Normal pending reservations、未来 scheduling intervals、未到达的 source evidence 是可接受的
运行状态，不是自动需要删除的遗留。多 Group decomposition、完整世界 Belief、融合、空间
交通图与模型策略属于后续能力选择，只有实现和证据明确时才更新为完成。

历史原始审计与核验产物位于仓库外的审计目录，真实结果不纳入 Git。本文件会随各软件
切片完成更新；原 ADR 的物理限制和未证明项也会同步保留。

## 本轮最终软件检查

S1–S6 的上述软件子集与原文档同步完成。最终 workspace/all-targets/all-features：
681 Rust tests passed，2 个既有长程 ownership checks 保留 ignored；Clippy、Rustdoc、
Rust/Python format、Ruff、strict mypy、function docs、完整 pytest 和 strict MkDocs 通过。
E6 未启动，hardware/model/Habitat 未运行；不把这些软件检查当作物理 release 或规模结果。

最终真实三 replica/Controller/two Node 检查以 binary
`d8945354eb7fde63d355ed41b3c77541b828541b728edbf4a72d780d59a0d9ff` 执行，minority
startup、old epoch route closure、leader death/Unknown/ownership/priority/journal、45 rows
exact metadata rejoin、standalone downgrade、quorum loss/restore 全部通过，原 Local EAIOS
调用数保持 2。真实 HTTP future preemption 与 pending Commit/SQL rollback 独立回归通过。
原始记录在仓库外 `adr-follow-up-audit-2026-10-12/final-replica-preemption/`，之前所有失败
也保留。具体未完成族仍按上表列示；没有把物理抢占、跨主机部署和其他独立能力标成完成。
