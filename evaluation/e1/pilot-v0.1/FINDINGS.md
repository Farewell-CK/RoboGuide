# E1 Overnight Run FINDINGS — B1 Closure Attempt + Mobility Pilot Decision

- Baseline: `37ff913`；夜间窗口 2026-09-16T17:19Z 起。Core/contracts 零改动。
- 执行轨迹：B1 RoboGuide 路径建成后连续 smoke，遇到 4 类 infrastructure 问题
  （全部窄修复并保留证据），最终撞上一个**真实的 deployment 语义缺口**，
  按 §29 停止 pilot 进入汇总。

## Research Question

Does RoboGuide's end-to-end organization path execute the same heterogeneous
mobility workloads under the Controlled Protocol — with the high-level task
entering RoboGuide as **text**, planned by the real Mission Intelligence?

## B1 数据（全部 fresh，全部保留）

| Run | 结果 | 分类 |
| --- | --- | --- |
| b1-rg run1 | MI Interpreter 调用 `timed out`（90s 预算） | MODEL_INFRASTRUCTURE（INVALID_INFRA；证据被 run2 覆盖，仅存日志行） |
| b1-rg run2 | Interpreter 成功（assessment 已产出），**Planner `timed out`** | MODEL_INFRASTRUCTURE（INVALID_INFRA）；`infra-failures/b1-roboguide-timeout-run2-*` |
| — | **窄修复**：`config/mission.toml` `timeout_seconds` 90→600（仅 MI 传输预算；model identity/sampling/Stage2 冻结面未动） | fix(eval) |
| b1-rg run3 | MI 全链成功：Interpreter→Planner 产出**双任务计划**（reach_object→any_targets\|0、reach_goal→TARGET_any_targets\|0），但卡 `AwaitingApproval` | 配置缺口：approval rules 是**风险门**（匹配=必须人工审批），非白名单 → **窄修复**：B1 deployment 配置声明 `approval_required_contracts=[]`（纯仿真导航，deployment 决策免审批） |
| b1-rg run4 | 计划被 Control **接受**（lifecycle=Accepted、ExecutionGroupCreated、双任务 Ready），但 node-a 的 assignment 被桥判 'pair never assembled' | 场景 barrier 竞态：420s pair 窗 < MI 规划时延 → **窄修复**：pair-wait 1200s；`infra-failures/b1-barrier-race*` |
| b1-rg run5 | 桥根本没收到 dispatch：**残留旧 server 占 25060**，新 server `AddrInUse` 崩溃，健康检查被旧进程应答 → 跨 run 污染 | **窄修复**：两个 scenario 脚本加端口 hygiene（只杀我们自己的二进制进程 + 失败即断）；v5 空日志即此 |
| b1-rg run6（v6，最终） | MI→Control 全链成功；**两个任务被 Control 合法打包到 node-a**（`SchedulingSelected` 两条均 node-a、resource_ids=[]）；共享世界端点容量=1 → 第二个 assignment 无法被消费 → mission 永久 Running（reach_object Active / reach_goal-receptacle Ready），桥在 1200s 窗口耗尽后把 node-a 判 'pair never assembled' | **B1 EXECUTION BLOCKER（integration/deployment 语义缺口，非 Core blocker）**；`infra-failures/b1-packing-blocker-run/` |

EMOS B1 arm：无需新代码——official evaluator 天然就是 Stage1→Stage2；paired smoke
已验证（seed 40 → episode 51，pddl_success=true）。

## Findings（只写数据直接支持的）

**Finding 1 — Mission Intelligence 消费高层任务的能力被证实。** 冻结的
b1-input（无分解、无 assignment 的纯语义指令）经真实 MI 全链
（Interpreter→Planner→Reviewer/Repair→acceptance）自主产出**合法的双任务
MissionPlan**：两个任务、两个 destination 恰为官方双 goal 实体
（`any_targets|0` / `TARGET_any_targets|0`），Control 接受并创建 Group、注册
双任务。plan 证据：`infra-failures/b1-packing-blocker-run/b1-request-record.json`。

**Finding 2 — B1 与 B2 出现了一个真实的语义差异。** B2 的 authored plan 为每个
role 声明 `resources: [space×1]`，迫使 Control 把两个任务分布到两个 Node
（run1 交叉分配实证）；**MI 生成的 plan 未声明任何 resource**
（`assignments.resource_ids=[]`），Control 因此**合法地**把两个任务打包到同一
Node——行为符合其语义（无排他性声明时允许打包），但 shared-world 部署的
endpoint 容量为 1（每 agent 一端点），第二个 assignment 无法被消费，mission
永久 Running。失败归因：`failure_location = integration/deployment boundary`；
`immediate_cause = scheduler packing without exclusivity claims vs per-agent
endpoint capacity`；`root_cause_status = confirmed`（非 Core 缺陷、非纯模型
失败）。证据：`infra-failures/b1-packing-blocker-run/{b1-request-record,
mission-final,events}.json`。

**Finding 3 — 官方 mobility benchmark 全部为跨楼层任务。** 99 集
`same_floor=false` 全真（census 后采样器复核），"floor diversity" 在该
benchmark 内不存在；RNS audit R2 的"全集可交换性是经验问题"仍待 pilot，
但今晚 pilot 因 Finding 2 未启动。

**Finding 4 — MI 与 Stage2 使用同一 relay/model（gpt-5.6-luna）**，模型公平性
边界保持；MI 的 90s 传输预算与 relay+xhigh 延迟的交互是 RoboGuide organization
配置问题（已修至 600s），与 EMOS 无关。

## 1-hop implications

- supported：B1 语义等价性（同高层任务→各自组织→同 Stage2 边界）在
  "计划被 Control 接受" 为止的链路上成立；执行层被 Finding 2 阻断。
- possible：若 assignment/resource 语义补齐，B1 pilot 可直接复用今晚全部
  资产（脚本/配置/verifier 均已就绪且经过失败路径验证）。
- unresolved：MI 为何不声明 resources（planner schema 无此引导？）——需要
  Codex 决策。

## Questions exposed（下一轮回答）

1. Mission 侧应如何表达 "exclusive per-node execution slot"：planner schema
   增加资源引导，还是部署侧 actor placement 支持通配 mission_id？
2. Shared-world endpoint 是否应支持同 Node 上排队多个 assignment（顺序执行于
   同一 agent）——语义上 any_at 允许单 robot 完成双 goal。

## BLOCKER 报告（交 Codex / 或后续 ZCode integration 立项）

- **类型**：INTEGRATION/DEPLOYMENT BLOCKER（非 Core semantics defect：Control
  行为符合 Proposal→Commit→Bind 与 scheduler 语义）
- **benchmark evidence**：`infra-failures/b1-packing-blocker-run/`
- **current contract limitation**：MI plan roles 未声明 exclusive 资源；
  Control 无 spread 约束依据；shared-world endpoint 容量 1。
- **minimal semantic requirement（三选一，需决策）**：
  1. Mission Planner 为导航类 role 声明 exclusive slot 资源（deployment/
     catalog 引导，或 planner schema 增加 resource 默认策略）；
  2. actor placement 文件支持通配/运行时 mission_id 绑定（Core schema 变更）；
  3. shared-world endpoint 支持同端点多 assignment 排队（integration 侧，
     语义为"同 agent 顺序执行两个 subtask"）。
- **affected workloads**：B1 mobility pilot 全部 12 对。

## Pilot 决定

**E1-I PILOT = BLOCKED（pre-pilot）**。episodes.json（12 对，seed 映射已
验证）已冻结于 `episodes.json`；blocked 原因见上。未启动任何 pilot pair，
无部分数据污染。

## 交付物

- `scenarios/e1-shared-world-episode-51/b1-input.json`（冻结高层输入）
- `scenarios/e1-shared-world-episode-51/{run-b1-roboguide.sh, verify-b1.py,
  mission-service-b1.toml}`（B1 场景资产，含全部夜间修复）
- `integrations/habitat-local-eaios/`：桥接受 `mobility.move@v1`（与 navigate
  同栈执行）；`evaluation/src/.../systems/roboguide.py`：shared-world verdict
  支持（上轮 paired smoke 已入库，本轮无改动）
- `evaluation/e1/pilot-v0.1/{episodes.json, inputs/, infra-failures/,
  run-pair.sh}`（pilot 冻结件与 runner，待 blocker 解除即可执行）
- `config/mission.toml`：timeout_seconds 90→600（infra 修复，已申报）
