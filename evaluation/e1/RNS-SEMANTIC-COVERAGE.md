# RNS Semantic Coverage Audit — EMOS/Habitat-MAS Workloads vs RoboGuide Node Abstractions

- Baseline: `110cb3f`（Census 之后）。方法：合同/ADR/源码阅读 + PDDL/dataset/robot-config
  静态提取；零 LLM、零 benchmark 运行、零 Core 修改。
- 机器可读版：`rns-semantic-coverage.json`。数据规模依据：`DATASET-INVENTORY.md`。

## 1. RNS abstraction model（以代码与 Accepted ADR 为准验证）

RNS = node-side substrate：把任意 deployment-owned Local EAIOS/Runtime 投影为
RoboGuide Node。四类 projection 全部真实存在（file:line 级证据）：

| Projection | 组件 | 证据 |
| --- | --- | --- |
| A. Execution | CapabilityProfile + readiness；OperationSupport；Operation Binding（operations.workflow）；Resource；Execute/Status/Cancel；local handle；durable journal（含 dispatch authorization 与 reconciliation_required 状态） | `contracts/node/v0.9/roboguide-node.proto:90/100/112/229/238/261`；`core/node-service/src/config.rs:419/436`；`store.py`（RoboGuide 侧 bridge 印证 journal 形态）；C1 系列 run 的 node journal |
| B. State | LocalSystemDescriptor + per-local-system health；Sensors（owner）；StateExport（owner + payload_schema + valid_for_ms/interval + source_observed_at/confidence pointer）；freshness=receive-relative | proto:74/105/137/203；ADR-0019；config.rs:69-98 |
| C. Coordination | PeerChannelReadiness（group/context/role-scoped）；固定只读 PeerChannelObserver | proto:215；config.rs:103-110；ingestion/liveness 消费路径 |
| D. Memory/Data | MemoryProviderDescriptor（kind/scope/visibility）；artifact staging/finalization + ArtifactBlobStore | proto:171；node journal `artifact_*` 表；ADR-0021 |

共同基础：Node/LocalSystem identity、Node Protocol v0.9 session/heartbeat/lease、
reconnect、durable command receipt（CommandPersisted ≠ execution outcome）、
reconciliation（Unknown → RecoveryRequired）——全部在 proto 与 `core/node-service` 落地。

**RNS OWNS**：semantic projection、local integration workflow、durable node-side
execution boundary。
**RNS DOES NOT OWN**：global Matching / Scheduling / Proposal→Commit / Recovery
authority / Mission semantics / Local planning / motion control / hardware control /
final safety。与 ADR-0021/0035/0036 一致，未发现偏差。

## 2. Capability vs Operation vs Local How（含 mobility.navigate@v1 同名问题）

合同现实：`contracts/capability/v0.3/catalog.json` 中
`mobility.navigate@v1` **同时**出现在 capabilities（provider feasibility 证据，
零属性）与 operations（canonical semantic What，`required_capabilities` 指回该
capability）两节。同名但两层：

- Capability `mobility.navigate@v1` = "本 Node 有能力执行移动类任务的可行性证据"
  （readiness + attributes；当前无属性）。
- Operation `mobility.navigate@v1` = "本 Node 可**接受**这个 canonical 语义操作"
  （Operation Support；ADR-0036：capability-compatible 但无 operation 的 Node 在
  Proposal 前即被排除）。
- Operation Binding = deployment-private 的 `[[operations]]` 配置，把 intact
  ExecutionIntent 映射到 Local How（E1 中即 EMOS Stage2/skill 栈）。
- Local How = EMOS skill / OracleNav / ROS / vendor API——对 RoboGuide 不可见。

结论：**不是** Capability == Operation == Skill 混同；同名是命名巧合，合同结构、
校验时机（Matching 用 capability，Proposal/Commit 前置 operation support）与
ADR-0035/0036 都按两层处理。审计判定：boundary SOUND。

## 3. EMOS workload 需求提取（证据驱动，不按任务名猜测）

决定性证据：`is_any_robot_at`（`habitat-lab/.../multi_task/pddl_sim_state.py:412-424`）
与 `is_object_detected`（同文件 :402-410）都**遍历全部 robot、任一满足即为真**——
官方 goal 谓词语义大规模地是 agent 无关的。逐族：

| 族 | datasets（episodes） | 全局 assignment 相关需求 | 证据 |
| --- | --- | --- | --- |
| Mobility（navigate） | mobility_episodes_1（99） | 无 provider 依赖：① `any_at` agent 无关；② Spot/Fetch locomotion 配置仅 `articulated_agent_type` 不同（无 climb/stairs/navmesh 差异属性），共用 navmesh+base_velocity；③ 跨楼层（ep51 `same_floor=false`）由**任何**被分配 agent 承担，无"某 robot 物理不可达"证据；④ 官方 solution 的 agent 分工是参考 convention | pddl_spot_fetch_mobility.yaml；robot_configs/mp3d/mobility_episodes_1.json；两 agent yaml diff |
| Perception（detect） | height_per（113）/spot_drone_per（154）/multi_agent_per（30） | 成功事实 = `is_detected`，逐 agent `agent_{i}_detected_objects` sensor 观测，任一 agent 检出即真；sensor 套件/视点属 per-agent runtime——**运行时观测事实（STATE），非全局先验 CAPABILITY** | pddl_sim_state.py:402-410；config_spot_drone_per.yaml lab_sensors |
| Manipulation（pick/place） | dist_man（89）/height_man（166）/fetch_stretch_man（180）/multi_agent_mobility（100）/multi_agent_man（30） | `at(obj,goal)` 需要物理 pick+place（arm+grasp+payload），Spot/Fetch/Stretch 有 arm（ik_arm_urdf），Drone 无 arm；`not_holding` 是持物状态判定。这是 provider-level feasibility → **CAPABILITY/OPERATION 层**，RoboGuide Catalog 已有 `manipulation.grasp@v1(max-payload-grams)` 与集成操作 `object.relocate@v1` | pddl_*.yaml；agent yaml `ik_arm_urdf`/arm sensors |
| Agent-bound goals | multi_agent_man/per | `robot_at(x, agent_i)` 绑定**场景内 robot 实例**；在 RoboGuide 中 Node 即 robot 实例 → 由 Mission Actor placement（deployment-owned，narrow first-use matching）表达，**不是**新 capability 属性 | pddl_multi_agent_man/per.yaml；AGENTS.md actor placement 语义 |
| Drone 飞行 vs 地面 | per 族 | goal 语义仍 any-agent；embodiment 差异不改变 assignment legality → LOCAL_HOW/metadata，**禁止**因此造 `robot_type` 枚举 | 同上谓词证据 |
| Compute | 无官方 multi-agent workload 要求 | NOT_NEEDED（compute.infer 不被任何 goal 谓词需要） | dataset-inventory |

## 4. Coverage verdict 主矩阵

| # | Benchmark requirement | Source | Needed abstraction | Current RoboGuide representation | Coverage | E1 blocker | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | 两 robot 各自导航到语义 checkpoint（any_at） | spot_fetch_mobility | OPERATION(mobility.navigate@v1)+CAPABILITY(同名 capability) | Catalog capability+operation、OperationSupport、Binding 全有；E1-I 2/2 实证 | EXPRESSIBLE | false | catalog.json；evaluation/e1/shared_world/runs/run{1,2} |
| R2 | 跨楼层/楼梯可达性差异 | ep51 same_floor=false, Δh=2.87 | `any_at` 只证明 goal 不绑定 agent；**不证明**两 embodiment 对全部 99 集目标物理可达性相同。现有静态证据不建立 provider 相关的 mobility feasibility 区分；已验证的 episode-51 情形（run1 交叉分配后仍 pddl_success=true）无需额外 mobility 属性；全集可交换性是 pilot 的经验问题 | mobility.navigate@v1 零属性保持（不新增属性） | EXPRESSIBLE（限于已验证 episode-51 情形） | false | pddl_sim_state.py:412；shared-world run1 verdict；**全集可达性证据：无（经验问题）** |
| R3 | 官方 solution 的 agent 分工（agent_0→object 楼层） | pddl solution 字段 | NOT_NEEDED（参考解，非约束）；RoboGuide 由 Matching 自由分配 | Matching 自由分配（run1 交叉分配实证） | EXPRESSIBLE（NOT_NEEDED 层已正确不建模） | false | shared-world run1 evidence |
| R4 | 感知型 goal（is_detected） | height_per/spot_drone_per/multi_agent_per | OPERATION(observe/detect) + 成功事实=STATE | **Catalog 无 perception 类 canonical capability/operation**；成功事实本应走 StateExport/observation | PARTIAL（What 无法作为 canonical intent 携带；能力证据层缺失） | false（非 E1-I）；P1 for 该子集 | catalog.json 无 perception.*；pddl_sim_state.py:402 |
| R5 | 操纵型 goal（at=需 pick+place） | dist_man/height_man/fetch_stretch_man/multi_agent_mobility/multi_agent_man | OPERATION(object.relocate@v1)+CAPABILITY(manipulation.grasp@v1, max-payload) | Catalog 已有；Local EAIOS 侧无 declarative pick/place workflow 实现 | PARTIAL（抽象在，集成未做、未验证） | false（非 E1-I）；P1 for 该子集 | catalog.json；agent yaml ik_arm_urdf |
| R6 | 持物/释放约束（not_holding、holding） | 同上 | LOCAL_HOW + 成功判定属 benchmark | 同上（object.relocate 的集成本地序列内部） | PARTIAL（同 R5） | false | pddl yaml |
| R7 | agent-bound goal（robot_at(x, agent_i)） | multi_agent_man/per | MISSION actor placement（非 capability） | Actor placement constraints narrow first-use Matching（deployment-owned） | EXPRESSIBLE | false | pddl_multi_agent_man/per.yaml；AGENTS.md actor placement |
| R8 | Drone 飞行 vs 地面 locomotion | per 族 | LOCAL_HOW/metadata | 无 robot_type 枚举（正确）；kind 字符串承载 coarse 类别 | EXPRESSIBLE（正确地不进全局层） | false | agent yaml；谓词 any 语义 |
| R9 | 互斥持物（同对象不能两人同持） | manipulation 族 | 跨 Node 对象级互斥属 **Control-owned Resource**（global reservation authority）；`local_locks` 仅是 Node-local 并发/安全保护，二者不同层。当前官方 Independent manipulation workload 不要求 RoboGuide 显式做 object-level cross-node reservation → **NOT REQUIRED BY CURRENT TESTED SEMANTICS**（跨 Node 互斥机制本身存在于 Control Resource 抽象中，EXPRESSIBLE） | 未来 shared-object/handoff/tightly-coupled workload 是否需要 object-scoped global resource = 后续 evidence-driven 问题 | NOT_NEEDED（NOT REQUIRED BY CURRENT TESTED SEMANTICS） | false | pddl yaml；node.toml local_locks；ADR-0021 Resource 语义 |
| R10 | compute 资源 | 无 workload | NOT_NEEDED | compute.infer@v1 存在但无 goal 需要 | NOT_NEEDED | false | dataset-inventory goal 列 |
| R11 | 跨 agent 状态共享/视图 | 官方集全部 Independent（census） | COORDINATION：无（relations=[]） | PeerChannel/relations 机制存在但官方集不需要 | NOT_NEEDED（机制就绪） | false | workload-taxonomy；PeerChannelReadiness proto |
| R12 | episode 级成功判定 | 全部 | BENCHMARK_ONLY（habitat.pddl_success 留在 harness） | 已按此实现（E1-I verdict 不拿 Mission Completed 冒充） | EXPRESSIBLE / 正确分离 | false | verify-shared-world.py negative case |

## 5. RNS evidence matrix

| RNS abstraction | Designed | Unit-tested | E1 validated | Real-hardware validated |
| --- | --- | --- | --- | --- |
| Node identity / LocalSystem identity | ✓（proto; config） | ✓（node-service tests） | ✓（两 Node 同宿主） | ✗ 未验证 |
| Capability Profile | ✓（ADR-0035） | ✓ | ✓（mobility 单一 profile） | 部分（C1 前期 real-node smoke 只验握手，未验 readiness 面向真实硬件的全链） |
| Capability attributes | ✓（ADR-0035 scalar） | ✓（contract tests） | ✗（E1 用空属性 profile） | ✗ |
| Operation Support | ✓（ADR-0036） | ✓ | ✓（exact-operation 门控通过） | ✗ |
| Operation Binding / Local How | ✓（ADR-0021/0036） | ✓（workflow 编译测试） | ✓（direct-Oracle 与 Stage2 两种 Binding） | ✗ |
| Health / heartbeat / lease | ✓ | ✓ | ✓（含 GIL 饥饿教训后子进程化修复） | ✗ |
| Readiness observation | ✓（ADR-0019） | ✓ | ✓（HTTP readiness 门） | ✗ |
| Resource（Commit 资源） | ✓ | ✓ | ✓（habitat-navigation-slot） | ✗ |
| State Export / freshness | ✓（ADR-0019/0035） | ✓（config/编译与转换测试） | ✗（E1 全程未配 state_exports） | ✗ |
| Memory Provider / Artifact | ✓（ADR-0021） | ✓（部分） | ✗ | ✗ |
| Peer Channel | ✓ | ✓（协议测试） | ✗（relations=[]，peer_channels=[]） | ✗ |
| Multi-local-system per Node | ✓（Vec<LocalSystemConfig> + owner 唯一性） | 部分（config 编译） | ✗（每 Node 恰 1 local system） | ✗ |
| Durable journal / exactly-once / reconciliation | ✓ | ✓ | ✓（含 C1-S1 fault matrix） | ✗ |

## 6. Episode 51 实际验证边界（诚实清单）

**VALIDATED**：逻辑 Node 抽象（2 独立 Node 同宿主）；Node Protocol 注册/会话；
LocalSystem health + capability readiness 基本 path；Operation Support 门控；
Operation Binding（两种 Local How）；canonical ExecutionIntent 全链保留；Execute/
Status/Cancel；stable local handle；durable journal；exactly-once dispatch（双端
auth=1）；多 Node shared-world 执行；原子 submit/取消；官方 pddl_success 权威分离。

**NOT VALIDATED**：异构 typed capability differentiation（两 Node 的 capability
attributes 完全相同——Spot/Fetch 底层 embodiment 不同 ≠ heterogeneous matching 被
验证）；multi-local-system 聚合；单 Node 多 operation；动态 readiness 迁移；
State projection；Memory/Artifact projection；Peer Channel；真实 spatial resource
协调（见 §7）；Compute scheduling。

## 7. Resource 语义 hygiene（episode 51 的 slot 审计）

`habitat-navigation-slot-a/b`（kind=space, capacity=1）判定为 **synthetic/bootstrap
resource**：无任何几何/topology 语义附着；两个 Node 在同一物理共享世界中各自"独占"
一个 slot（物理上共享同一空间），故它**不是**真实 global spatial resource，而是
Control 资源承诺管道的执行/并发槽位。`required_resources`（Control committed、进
invocation.resource_ids）与 `local_locks`（habitat-simulator-* 纯 Node-local 并发
保护）语义区分清晰——后者**不得**在论文中表述为 global resource scheduling 证据。
本轮不改生产代码；建议后续给该 slot 改名/标注为 bootstrap（命名级改动，非本轮）。

## 8. Node vs Physical Host

现状表述："每台节点机器只部署一个 roboguide-node"（ADR-0010:9）、"运行一个通用
roboguide-node"（docs/architecture/v2/README.md:121）、"Each node machine runs only
roboguide-node"（AGENTS.md）。E1-I 在单物理机上运行 2 个逻辑 Node 全链成功——
**Node 是独立的 execution/capability/liveness authority boundary**，与物理机不是
一一绑定。已按建议定义做 docs-only 澄清（见 §11 修订记录），语义未动。

## 9. Multi-local-system

Config 层完整支持一个 Node 挂多个 local system（`Vec<LocalSystemConfig>` +
connections 归属 + capability/operation/resource/sensor/state_export/observer 的
唯一 owner 约束）→ **DESIGN SUPPORTED**。E1 所有 run 每 Node 恰 1 local system →
**EXPERIMENTALLY UNVALIDATED**（健康聚合跨 local system 的行为未在真实多系统下
观测）。不得写成已验证 interoperability。

## 10. Findings severity

| ID | 发现 | Severity |
| --- | --- | --- |
| F1 | Catalog 无 perception 类 canonical capability/operation（is_detected 型 goal 的语义 What 无法作为 ExecutionIntent 携带） | **P1**（阻断 297 集 3 个感知子集；非 E1-I blocker） |
| F2 | 操纵型 workload 的 Local How（pick/place declarative workflow）未实现/未验证（Catalog 抽象已在） | **P1**（阻断 565 集 5 个操纵子集；非 E1-I blocker） |
| F3 | `habitat-navigation-slot` 是 synthetic bootstrap resource，文档/证据若写成 physical-space scheduling 会过度声明 | P2（命名/表述级） |
| F4 | Capability attributes 全空（当前忠实；若未来出现真实 provider 依赖 nav feasibility 需结构化属性——ADR-0035 已预告 scalar 限制） | P2 |
| F5 | State/Memory/Peer 三类 projection 设计+测试在、E1 证据缺失 | P2（claim 强度限制） |
| F6 | Multi-local-system 未实验验证 | P2 |
| F7 | Node==物理机 的文档表述过强 | P2（本轮已 docs 澄清） |
| F8 | reverse/fourth 5 config 数据作者未发布 | P3（非 RNS 问题，census 已记录） |

无 P0。

## 11. 本轮修订记录（docs-only）

- `docs/architecture/v2/README.md`：Node 表述改为"独立 execution/capability/
  liveness authority boundary；典型物理部署每设备一个 RNS，仿真/特殊部署可在同一
  物理机承载多个逻辑 Node"。
- `docs/decisions/0010-...md`：追加同义澄清注记（不改 Accepted 决策内容）。

## 12. 最终回答（§14）

**A. Current RNS core abstraction** = **SOUND**（含两处小澄清：Node≠host 表述、
synthetic resource 标注；无 structural gap。证据：§1 全 projection 存在、§3 谓词
语义、§4 矩阵无 MISSING 项）。

**B. Formal E1-I episode 51 仍然有效？** = **YES**（其 claim 边界按 §6 清单收窄
后完全成立；本轮唯一相关修订是 Node 表述澄清）。

**C. 多少 workload family 忠实可表达？** = 9 个被引用 spec 归并为 **3 个 family**：
navigation **1/3 fully**（99 集）；perception、manipulation **2/3 partial**。

**D. 不能忠实表达的官方 workload？** = perception 族（297 集，缺 canonical
observe/detect operation——F1）；manipulation 族（565 集，抽象在、Local How 集成
缺失——F2）。reverse/fourth 变体（5 config）是数据不可得问题，非 RNS 问题（F8/P3）。

**E. 首个 Formal E1 pilot 前是否有 blocker？** = **无**。E1-I（episode-51 族）
全部 EXPRESSIBLE；F1/F2 只在扩展 workload 时成为该子集的 P1。

**F. 建议 = 1. run E1 now**（E1-I 范围，episode-51 族 99 集采样）。证据路径：
本文件 §4 R1-R3/R12 全 EXPRESSIBLE + `shared_world/runs/*`。F1（perception
operation 的 Catalog 增补）与 F2（pick/place workflow）作为 E1 扩展阶段的前置，
**F1 需要决定后走 Catalog/contract 升级（owner: Codex Core/contracts），F2 是
integration/eval 侧（owner: ZCode）**——两者都不是现在的 blocker，不提前设计。
