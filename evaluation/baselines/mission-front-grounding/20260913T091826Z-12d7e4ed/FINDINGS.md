# Grounding A/B Canary Findings — 2026-09-13

- Suite: `mission-front-20260913T091826Z-12d7e4ed`（本目录）
- Baseline: `main@ab0603c`；Prompt / mission production code / canary fixtures 运行期冻结
- Model: `gpt-5.6-luna`（reasoning_effort=xhigh，经中转）
- Run: 7 组 A/B canary × 2 臂 = 14 情景，一次性串行完成，未中途修改任何东西
- 结果: observe-only 口径 **14/14 通过**；全部 14 情景终态 = NeedsClarification
- 成本: 14 次 LLM 调用（全部 Interpreter 阶段），input ≈ 11.4k / output ≈ 15.6k tokens
- Per-case stage timing 正常（interpreter 13.8s/次量级，planner/reviewer/repairer 0 次）

## GF-1（正面）: grounding 证据被真实消费，提问从"开放探索"转为"证据锚定"

各组 A/B 的澄清问题数与性质变化：

| pair | context_free | grounded | 变化 |
| --- | --- | --- | --- |
| fresh-unique-place | 2 问（哪个前台？交付给谁？） | **1 问**（仅剩交付完成判据） | 目的地问题被 Fresh 证据**直接消解** |
| conflict | 3 问（哪个会议室/交付点/哪块白板擦） | 2 问（**"请确认是 3F 西还是 2F 东？"** + 交付点） | 冲突被显式作为确认问题提出，未瞎选 ✓ |
| stale | 3 问（开放问哪个桩） | 3 问（**"是否为标注 B1 的充电桩？不是请提供"**） | 数量同，但锚定到证据候选，性质更好 |
| no-evidence（对照） | 4 问 | 1 问 | 空证据 ≈ 对照组（见 GF-5 方差） |
| metadata-only | 5 问 | 5 问 | 见 GF-2 |
| fresh-vs-stale | 3 问（开放问卸货区） | **1 问**（"卸货区 A（闸口A）还是 B（闸口B）？"） | 模型列出两个候选请求裁决，**未擅自采用 Fresh 压制 Stale**——与 observe-only 设计一致，未隐含任何 fusion policy ✓ |
| gap-only | 3 问 | 3 问（实质相同） | gap 未降低行为质量（fail-soft 生效）✓ |

## GF-2（正面）: MetadataOnly 边界被正确遵守

g5 grounded 臂的澄清原文：**"当前信息不足以确认地图内容及其适用性"**、
"不能据此推断具体地图修订版"——模型把 MetadataOnly 清单当作"存在性证明"而非
内容证明（ADR-0037 语义），无任何地图内容越权宣称。output tokens 显著增加
（2663 vs 1781），说明模型对 memory 证据做了更长的显式推理。

## GF-3（正面）: gap fail-soft 生效

g7 双源不可用（2 个 durable GroundingGap）不阻断、不贬质：问题集与对照臂实质
相同，行为等同无证据场景。

## GF-4（核心缺陷复现 + 新证据）: Interpreter 从不返回空 open_questions

**14/14 情景全部 ≥1 个澄清问题并停在 NeedsClarification；Planner/Reviewer/
Repairer 调用次数 = 0（per-case stage timing 证实）。** 即使 grounding 完全
回答了目的地（g1 grounded），模型仍提出"交付完成判据"类问题而非以假设前进。
这把 F-1 的三根因结论从 29-case baseline 复制到了 grounding-aware 场景：
**engine 零阈值阻断 + prompt 无前进规则 + schema 必填 open_questions，使前半链路
在当前形态下结构性不可达 Planner。** 且新增的 grounding 规则（冲突/过期须问）
给了模型更多提问的正当理由——g2/g3/g6 的锚定式提问正是新规则与旧零阈值共同作用
的形态：**提问质量提升，但"永不收敛到可执行"未变。**

## GF-5（测量限制）: 问题数量存在单样本方差

g4 空证据对照臂 Q=1 vs context_free Q=4——两者输入几乎同构（空快照 vs 无
快照），差异只能来自模型采样方差。含义：**canary 的问题"数量"结论需多采样**；
本轮可信的是定性位移（问题内容锚定证据候选，七个 pair 方向一致）。

## GF-6（Harness 缺口，仅报告）: `run_grounding_suite` 的 summary 未聚合 stage_timings

per-case `stage_timings` 正常落盘（见上），但 `run_grounding_suite` 自建的
summary 没有 suite 级聚合字段（`stage_timings: null`）。属 evaluation 侧小修。

## 建议（供 review，未实施）

1. R1/R2（interpreter 前进规则 + 收敛语义）的必要性被 14/14 复现强化——
   grounding 修复了"问什么"，没修复"永远在问"；建议交 Codex 一起处理；
2. canary 若要出数量型结论，每情景跑 N≥3 采样（成本 ×3）或仅用定性位移；
3. GF-6 的 summary 聚合补齐属 evaluation 小修。

## 证据位置

- 逐情景完整记录：`cases/*.json`（instruction / dialogue / assessment /
  grounding_context digest + evidence 明细 / 每次调用 token+延迟 / stage timing）
- `summary.json` / `cases.jsonl`
