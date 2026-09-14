# 197cb01 Targeted Smoke Findings — Provider DTO / Reviewer / Under-ask Research

- Baseline: `main@197cb01`（strict planner DTO 适配 + 输出 normalize）
- Run: 2026-09-14，两组 suite 串行（grounded 10 情景 + plain 3 case），一次性完成
- Model: `gpt-5.6-luna` @ xhigh（配置未变）；Prompt / Mission production code /
  canary fixtures 运行期冻结；未根据中途结果做任何修改

## T-0（核心验收 ✓）: Provider strict DTO 被中转接受

**Planner HTTP 400 = 0**（此前 16/16 全部 400）。14 次 Planner 调用全部通过
中转的 strict structured-output 校验：

- 9 次成功产出并通过 canonical MissionPlan v0.7 parse + engine validation
  （`validate_implementation_support` + Catalog `validate_plan`）+ 进入 Review；
- 5 次 Planner 调用超时（见 T-2），非 schema 拒绝。

确定性 schema 测试无法覆盖的问题——**中转/上游对真实 strict DTO 的接受性——
已被本轮真实调用正面验证**。可见的任务结构样例（g4-grounded）：
`task-relocate-red-labeled-toolbox`（object.relocate 语义，destination=
装卸区）；n2 产出 `task-build-warehouse-map` + `task-publish-warehouse-map`
双任务，`execution_intent` 使用 canonical artifact 参数（build/publish +
map_id/revision_id/spatial_anchor_id）。

## T-1（新 production blocker）: Reviewer 调用 100% 超时

8 个 case 到达 Reviewer、共 9 次调用，**全部在 90s 超时**（
`mission review failed: timed out`）；Repairer 因此 0 次调用，0 个 case
到达 Accepted。xhigh reasoning + 完整 v0.7 plan schema 的 review 调用
经中转稳定超过 `timeout_seconds = 90`。属 `config/mission.toml` 配置与
Reviewer 调用策略问题 → 交 Codex / 项目负责人决策（调大 timeout、review
换独立模型/档位、或压缩 review 输入）。

## T-2: Planner 超时 5/14

g1-grounded、g5-free、g5-grounded、g6-free、h2 的 Planner 调用超时
（xhigh 下复杂 schema 生成 >90s）。与 T-1 同根：90s 预算对 xhigh 偏紧。

## T-3: Under-ask research evidence（保留，不判定）

- **g6-grounded**: Planner 在 objective 与 intent 参数中**静默选定
  "卸货区 A（Gate A）"**（即 Fresh 证据位置；Stale 证据指向 Gate B），
  未向用户确认，也未记录 fusion 决策。研究数据：grounding-aware 规划会
  按证据新鲜度自行消解冲突——是否可接受待项目负责人裁定（本 canary 为
  observe-only，不判定）。
- **a1-plain**: Interpreter 假设"移离桌面即可，具体位置由后续规划确定"
  并以空 open_questions 前进；Planner 使用占位 destination
  `placement-not-on-tabletop-selected-by-later-planning`——**未擅自虚构
  具体房间** ✓（诚实的占位处理），但"位置由后续规划决定"在当前架构中
  并无后续规划阶段承接，属语义缺口的研究样本。
- **g7-grounded**: 本轮对缺失的检查点身份提出了 Blocking 问题（上一轮
  同情景直接前进）——同指令跨 run 行为存在采样方差，记录待复核。
- **g5**: Planner 超时未产出 plan，MetadataOnly"是否声称已读取地图"
  本轮无法验证（上一轮验证过不会声称）。

## 其他观察

- n2 产出英文任务描述（prompt 为英文模板 + 中文指令），canonical 参数
  结构正确；中英文混合输出对下游无影响，记录备查。
- g1-free 的 plan 分解出 deliver + verify 两个任务（integrated relocate
  被 verify 需求拆分——i 系 integrated 案例的后续评测点）。

## 结论对照（用户成功判据）

| 判据 | 结果 |
| --- | --- |
| Planner HTTP 400 = 0 | ✅（14 次调用 0 次 400） |
| canonical MissionPlan parse/validation 可执行 | ✅（9 次产出全部 parse/validate 通过并进入 Review） |
| Reviewer calls > 0 | ✅（8 case / 9 次调用），但 100% 超时 → 修复前无 Accepted |

## 建议下一步（供 review，未实施）

1. T-1/T-2：`timeout_seconds` 90→300（config 层，属项目负责人决策）或
   Reviewer/Planner 降档 reasoning——二选一需实测；
2. Reviewer 打通后重跑本轮 targeted smoke，验证 Repair 循环与 Accepted 路径；
3. g6/a1 under-ask 样本交项目负责人裁定（fusion 政策与占位 destination 语义）。

## 证据位置

- grounded suite：`grounded-rerun` 同级 `197cb01-targeted/`（10 case）
- plain suite：`197cb01-targeted-plain/`（3 case）
- 每份含 instruction / dialogue / assessment（objective/constraints/
  assumptions/open_questions）/ plan（若产出）/ review history / lifecycle /
  issues / 逐调用 token / stage timings
