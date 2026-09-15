# 2de3171 Full-Chain Diagnostic Findings — Semantic Consistency Fixes Verified

- Baseline commit = `2de3171`（fix(mission): align semantic consistency rules）
- **Production default timeout = 90s（`config/mission.toml` 未改动，已复核）**
- **Diagnostic run timeout = 300s**（评测栈 `--llm-timeout 300` 内存级覆盖，
  plain-suite summary 记录 300.0；grounded-suite summary 事后补记同一值，
  原始 per-call latency 均为运行时真实数据）
- Model: `gpt-5.6-luna` @ xhigh；Prompt / Mission / Core / Grounding / fixtures /
  provider DTO 运行期冻结；本轮未根据中途结果修改任何东西
- 5 个 targeted case 串行：g3-stale-free、g4-no-evidence-free、
  g6-fresh-vs-stale-grounded（grounded suite）；a1-missing-destination、
  n2-mapping（plain suite）
- Transport 为 non-streaming，未测 TTFB / first-token latency（按要求未实现
  streaming）

## 顶层结果：5/5 通过，其中 4 个 Accepted —— Front-half 全链路首次真实走通

```text
Interpreter → Planner → Reviewer → Accepted        (g3, g4, a1, n2)
Interpreter → NeedsClarification                   (g6-grounded，符合设计)
Repairer 本轮 0 次调用（因 Reviewer 直接 Approve，无需 Repair）
```

## 判定重点逐条核验（用户第一优先级）

**1. g3/g4 旧 Reviewer semantic contradiction 归零 ✅**

上一轮（ae260a9）g3/g4 的 Review 各提出 2 个 issue（`unverified-physical-effect`、
`ungrounded-timing`，全部 `required_action=RepairPlan`）；本轮两个 case 的
Review 均 **approved=true、issues=[]**，一次通过。两处修复点的行为验证：

- `earliest_start_offset_ms: 0` 不再被判 ungrounded（新规则：0 是 canonical
  中性默认）；
- 纯物理 expected effect 不再强制 verifier（g3/g4 均保持
  `basis=execution-report, verifier=null` 且 Review 通过）。

**2. g6 停止 Fresh-wins silent fusion ✅（行为反转实证）**

上一轮：静默选定 Gate A（Fresh），直达 Planner；本轮：Interpreter 提出
**Blocking clarification**——"请确认当前卸货区是卸货区 A（gate A），还是
卸货区 B（gate B）？"，冲突保持未解析、停在 NeedsClarification。这正是
新规则（freshness 不得自动成为 truth/precedence policy）的预期行为；
objective 也相应保持中性（"所指的当前卸货区"）。

**3. a1 停止 later-planning destination escape ✅（行为反转实证）**

上一轮：占位 destination `placement-not-on-tabletop-selected-by-later-planning`；
本轮：objective = "将桌子上的杯子搬到**卧室的床头柜**上"，intent 参数
destination = 卧室的床头柜——语义终态完整、无任何 placeholder /
later-planning 逃逸，Review 一次通过。（注：本轮 Interpreter 未提问而直接
形成了完整 destination，与"缺 destination 应 Blocking"的预期路径不同——
模型自行补全了语义合理的终点。这是 under-ask 议题的新样本：终态完整、
无需澄清即可 Review 通过，但该 destination 非用户 supplied。记录为
observation，不判定。）

**4. Review→Repair→Re-review 收敛 ✅（以直接 Approve 形式）**

300s ceiling 下 Reviewer 4/4 成功（avg 41.8s / max 106.7s——max 已超 90s
production 默认，证明 ceiling 提升是必要的）；全部直接 Approve，Repair
循环未触发即收敛。**注意**：本轮没有观察到"Reject → Repair → Re-review"
路径（修复后的 Reviewer 不再产生旧 false-positive，而这些 case 又不足以
触发真 Reject）——Repair 收敛路径的验证需要新的真缺陷 case，留待后续。

**5. n2 干净 full-chain positive ✅**

无澄清（provider/robot 选择零泄漏，正确落入 assumptions："具体机器狗及
实现方式由系统根据能力进行选择"）→ Planner 产出双任务
（`spatial.map.build@v0` + `spatial.map.publish@v0`，canonical artifact 参数
含 mission 派生的 revision_id）→ Review approved → **Accepted**。

## Mission Intelligence Stage Breakdown（本轮 6 interp / 4 planner / 4 reviewer / 0 repairer）

| Stage | calls | success | timeout | avg latency | max latency | avg reasoning tokens | avg input tokens | avg output tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Interpreter | 6 | 6 | 0 | 12.0 s | 22.5 s | 464 | 1,574 | 542 |
| Planner | 4 | 4 | 0 | 58.9 s | 100.0 s | 2,684 | 4,868 | 3,144 |
| Reviewer | 4 | 4 | 0 | 41.8 s | 106.7 s | 2,210 | 3,943 | 2,230 |
| Repairer | 0 | — | — | — | — | — | — | — |

（成功调用均值；reasoning tokens 来自 usage.output_tokens_details.
reasoning_tokens。供后续 Planner vs Reviewer、xhigh vs 降档、per-stage
timeout policy 的比较基线。）

## 关键 latency 观察

- Reviewer max 106.7s、Planner max 100.0s——**均超过 production 默认 90s**，
  与上轮"90s 切在分布中部"的结论一致：300s ceiling 下超时归零
  （本轮 0 timeout，上轮同栈 11 次 Review 调用 9 次超时）；
- token 形态：Planner/Reviewer 的 reasoning tokens 占 output 约 85%/
  99%（xhigh 特征），Interpreter 则 reasoning≈output（问题生成本身）。

## 遗留观察（不判定，供 review）

- a1 的 destination 是模型补全而非用户 supplied（见判定重点 3）；
- g6-grounded 本轮正确 Blocking，但与上一轮"直接前进"构成跨 run 方差
  的又一实证（同指令同 fixture）；
- Repair/Re-review 路径本轮未被触发，其收敛性仍缺真缺陷 case 验证。

## 证据位置

- grounded suite（g3/g4/g6-grounded）：`grounded-suite/`
- plain suite（a1/n2）：`plain-suite/`
- 每份含 instruction / dialogue / assessment（objective/constraints/
  assumptions/open_questions）/ plan（含 per-task timing/satisfaction/
  execution_intent）/ review history / lifecycle / issues / 逐调用
  token（含 reasoning）/ 延迟 / stage timings
