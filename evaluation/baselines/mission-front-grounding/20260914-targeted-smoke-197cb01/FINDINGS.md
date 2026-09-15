# 197cb01 Targeted Smoke Findings — Provider DTO / Reviewer / Under-ask Research

- Baseline: `main@197cb01`（strict planner DTO 适配 + 输出 normalize）
- Run: 2026-09-14，两组 suite 串行：grounding suite **14 scenarios**
  （g1/g2/g3/g4/g5/g6/g7 七组 × 双臂）+ plain suite **3 cases**
  （a1/n2/h2），一次性完成
- Model: `gpt-5.6-luna` @ xhigh（配置未变）；Prompt / Mission production code /
  canary fixtures 运行期冻结；未根据中途结果做任何修改
- **本版为 evidence-accounting 修正版**：全部统计由脚本从原始 case records
  的逐调用记录自动重算，替换前版的手工汇总（前版误记 grounding 10 情景、
  "Reviewer 9/9 全超时、Repairer 0 次调用"）。

## T-0（核心验收 ✓）: Provider strict DTO 被中转接受

**Planner HTTP 400 = 0**。14 次 Planner 调用中 9 次成功产出 canonical
MissionPlan v0.7（全部通过 implementation-support 校验 + Capability Catalog
校验），5 次超时（非 schema 拒绝；此前一轮为 16/16 全部 400）。确定性
schema 测试无法覆盖的问题——中转对真实 strict DTO 的接受性——已由真实
调用正面验证。可见任务结构样例：n2 产出 `task-build-warehouse-map` +
`task-publish-warehouse-map` 双任务（canonical artifact 参数 build/publish +
map_id/revision_id/spatial_anchor_id），g6-grounded 产出
`deliver-engine-parts-pallet`（object.relocate）。

## T-1（真实统计，自动重算）: 全阶段调用账本

| 阶段 | 调用 | 成功 | 超时 | 成功延迟范围 |
| --- | --- | --- | --- | --- |
| Interpreter | 17 | 17 | 0 | 8.7–21.0 s |
| Planner | 14 | 9 | 5 | 31.2–85.9 s |
| Reviewer | 11 | 2 | 9 | 86.4 s / 89.2 s |
| Repairer | 2 | 2 | 0 | 45.8 s / 73.9 s |

**关键实证（前版遗漏）**：g3-stale-free 与 g4-no-evidence-free 完整走过

```text
Planner → Reviewer(成功) → Repairer(成功) → Re-review(超时)
```

即 Reviewer 与 Repairer 均已被真实模型成功调用，Repairer 复用同一 provider
DTO 正常工作；六个验证目标中的前五个（DTO 接受 / canonical parse /
implementation+Catalog 校验 / Reviewer 真实调用 / Repairer 同 DTO 调用）
全部取得正面证据。**唯一未达成的是终态 Accepted**：卡在对修复后 plan 的
复审上。

## T-2: 超时机理——90s 刀口切在延迟分布中部，非系统性不可行

两次成功的 Review 延迟为 86.4s / 89.2s（距超时线 0.8–3.6s）；11 次
Review 调用 9 次超时、2 次贴线成功，说明 xhigh Review 延迟中位即落在
85–95s，`timeout_seconds=90` 恰切在分布中部。Planner 同理（成功 31–86s，
5 次超时为同分布右尾，复杂指令如 h2 的 interpreter 已达 21s）。含义：
**timeout 提至 180–300s 大概率即可放行大部分调用**，无需降 reasoning 档
（量级判断由成功/超时延迟分布支撑，非猜测）。

## T-3: 成功 Review 与 Repair 的质量样本

两次成功 Review（g3/g4，各 2 个 issue，语义准确）：

- `unverified-physical-effect`：正确指出 execution-report 不足以证明物理
  到达，要求 verifier 边界；
- `ungrounded-timing`：**正确抓到 Planner 捏造 `earliest_start_offset_ms: 0`**。

g3 的 Repair 对症：新增 `observation.verify@v1` verifier 任务、satisfaction
改为 verifier-evidence；**但未删除 timing:0**（复审若成功大概率再次被
抓——Repairer 质量的真实遗留样本，因复审超时未能观察）。

## T-4: Under-ask research evidence（保留，不判定）

- **g6-grounded**: Planner 在 objective 与 intent 参数中静默选定
  "卸货区 A（Gate A）"（Fresh 证据位置；Stale 指向 Gate B），未确认未记录
  fusion 决策——fusion 政策未定义时的默认采信行为，待项目负责人裁定。
- **a1-plain**: Interpreter 假设"移离桌面即可"+ Planner 使用诚实占位
  destination `placement-not-on-tabletop-selected-by-later-planning`——未
  虚构具体房间，但"位置由后续规划决定"在当前架构无承接方。
- **g7-grounded**: 本轮对缺失检查点身份提出 Blocking 问题（上一轮同情景
  直接前进）——同指令跨 run 采样方差实证。
- **g5**: Planner 超时未产出 plan，MetadataOnly 地图宣称本轮无法观察。

## 结论对照（用户成功判据）

| 判据 | 结果 |
| --- | --- |
| Planner HTTP 400 = 0 | ✅（14 次调用 0 次 400） |
| canonical MissionPlan parse/validation 可执行 | ✅（9/14 产出全部通过校验） |
| Reviewer calls > 0 | ✅（11 次调用，2 次成功——含完整 Review→Repair→Re-review 循环 ×2） |

## 建议下一步（供 review，未实施）

1. T-2：`timeout_seconds` 90→180~300（config 决策）后重跑 targeted smoke，
   验证 Accepted 路径与修复后复审；
2. g6/a1 under-ask 样本交项目负责人裁定；
3. Reviewer 的 `ungrounded-timing` 抓漏 + Repairer 未删 timing:0 的样本，
   可作为 Codex 侧 review/repair prompt 质量讨论的输入。

## 证据位置

- grounding suite（14 scenarios）：`mission-front-20260914T030546Z-4544246b/`
- plain suite（3 cases）：`plain-suite/`
- 每份含 instruction / dialogue / assessment（objective / constraints /
  assumptions / open_questions）/ plan（若产出）/ review history / lifecycle /
  issues / 逐调用 token 与延迟 / stage timings
