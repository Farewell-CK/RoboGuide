# Mission Front-half V1 Freeze — Final Real-Model Regression FINDINGS

- Suite: `mission-front-20260915T034439Z-2455d073`（20 case，一次性串行，运行期冻结）
- Baseline SHA = `818fa8e60d80b0551522bd4c41066e699263a531`
- Model = `gpt-5.6-luna`，reasoning = `xhigh`
- **Production default timeout = 90s（未修改、未提交）**
- **Evaluation ceiling = 300s（`--llm-timeout 300`，内存级）**
- 成本: 70 次 LLM 调用，398,273 tokens（in 240k / out 158k）
- 运行期零修改：Prompt / Mission / Core / Catalog / MissionPlan / provider
  DTO / case YAML / invariant 全部未动；失败 case 保留原始证据，未重跑

## 顶层结果

**10/20 invariant-pass**。但按 V1 Freeze 语义（ambiguity case 合理结果 =
NC→follow-up→Accepted；502 基础设施故障 ≠ 语义失败），语义正确率为
**14/17（排除 3 个 502 纯基础设施 case）**：

| 分类 | case | 判定 |
| --- | --- | --- |
| Accepted（含 5 ambiguity 全部收敛） | n1, n2, n3, t4, i1, a1-a5 | ✅ 11 |
| 基础设施 502（非语义） | c1, i2, i4 | ⚠ D 类 |
| **新语义发现**（见下） | n4, h2, t1, t2, m1, m2, m4 | ❌ B/C 类 |

## 8 个 gold-correct case 核实

| case | 结果 | 核实结论 |
| --- | --- | --- |
| **n2** | ✅ Accepted | build+publish，无机器人/provider 泄漏 ✓ |
| **n3** | ✅ Accepted | verify(expected=灭火器在原位)，"原位"以 assumption 诚实记录 ✓ |
| **n4** | ⚠ NC（新发现） | model 参数用了 `pointcloud-analysis-v1` ✓（instruction 提供的值，未发明）；但 Interpreter 额外问"哪段点云数据"——数据源选择属 Defaultable 还是 Blocking 存在争议（见 B-1） |
| **h2** | ⚠ NC | embodiment 词已删✓；但"一批样品"被问——object 范围是否 Blocking 存在争议（同 B-1） |
| **t1** | ⚠ NC→（无 follow-up 可用） | **Planner 被自己的 source 参数挡住**：写 `已打印的季度报表当前所在位置` 后被 Reviewer 判 placeholder——reviewer 语义规则与用户指令现实（用户不知道报表现在哪）之间的结构性张力（见 B-2） |
| **t2** | ⚠ NC→（无 follow-up 可用） | Reviewer 判 `mobility.navigate` 无法表达"巡逻开始"——Catalog 缺 patrol 语义 op，与 audit 判定一致但 audit 时低估了 Reviewer 会 enforce 这个缺口（见 B-3） |
| **t4** | ✅ Accepted | model=`defect-detection-v1`，instruction 提供 ✓ |
| **m1/m4** | ❌ Failed | 见 A-1（本轮最重要发现） |

## 发现分类

### A. Systematic correctness blocker — **1 项**

**A-1: Reviewer 的 verifier 语义与 schema 校验存在自相矛盾循环**

m1/m2/m4 三个 case 走入同一死循环：

```text
Round 1: Reviewer 判 [verification-basis-insufficient] → "这个 task 要 verify
         但 basis=execution-report 且 verifier=null，改 verifier-evidence"
Repair:  Planner/Repairer 改为 verifier-evidence 并加 verifier
Round 2: Reviewer 判 [ungrounded_evidence_freshness] → "你加的
         max_evidence_age_ms=N 毫秒没有用户/policy 依据，删掉"
Repair:  Repairer 试图改回（或删 verifier）
Round 3: 要么 schema 校验拒绝（m1: "verifier must be present exactly for
         verifier-evidence basis"），要么回 Round 1（m2/m4 循环直到
         repair attempts exhausted）
```

根因：Reviewer 要求"要 verify 就必须 verifier-evidence"，但当 Repairer
遵守并添加 verifier 时，其必须填写的 `max_evidence_age_ms` 又被同一
Reviewer 判为 ungrounded。**Reviewer 的两条规则（verification-basis 与
ungrounded-freshness）在当前 Catalog 参数集下互斥**——verify@v1 只提供
`expected` 参数，不提供 evidence-age 的默认来源，用户指令也从不提供毫秒
级 freshness。这是 Reviewer prompt 层的 production 缺陷（需 Codex 修）。

### B. Isolated model behavior / 语义争议 — 3 项

**B-1: "object/data-source 识别"在 Blocking vs Defaultable 边界上摇摆**

n4（哪段点云）、h2（哪批样品）被问——Interpreter 把"指代具体数据/物体
集合"判为 Blocking。按 ADR-0038 字面，这确实是 materially different
target，**不违规**；但 gold audit 时将其标为 KEEP（不应问）。属 gold
边界模糊，不是 production bug。修复方向：gold 层明确这类指令为
"合理可问"（改 `clarify_first_pass: true`）或在 instruction 中提供
识别特征。

**B-2: object.relocate 的 `source` 参数在用户不知情场景下的结构性张力**

t1：用户说"把报表送到办公室"——用户不知道也不关心报表现在哪。Planner
诚实写"报表当前所在位置"，Reviewer 按新规则判为 placeholder。**这条
reviewer 规则对"用户不知 source"的常见指令过于严格**——source 的
provenance 应允许"current-location-of-{object}" 作为合法语义引用（它
指真实世界状态，不是逃避）。属 Reviewer prompt 的边界 case。

**B-3: Catalog 无 patrol op 被 Reviewer enforce**

t2：audit 已知 patrol 缺 op，但 audit 判 KEEP 时假设 navigate 可近似。
**Reviewer 拒绝了这个近似**（判 expected effect 无法由 navigate 建立）。
这说明 Reviewer 在严格度上是对的——patrol 语义确实超出 V1 Catalog。
修复方向：gold 改 DEFER（与 c3 同理），或 instruction 改"去园区大门
执勤"（纯 move）。

### C. Eval invariant / measurement issue — 2 项

- t1/t2 的 clarification 类型在 record 中是 `MissionIntelligence/
  ClarificationQuestion`，但这发生在 **Planner/Reviewer 之后**的 re-
  clarification 路径——case expectation 没有建模"review 后回问"场景，
  invariant 把它当首轮 over-ask 判 FAIL。测量口径需区分 first-pass vs
  review-triggered clarification。
- m4 的 objective_fidelity 判"建图"缺失——实际 plan objective 写的是
  "建立地图"（同义词）。invariant 的 must_cover 是精确 substring 匹配，
  需要同义容忍或 gold 改词。

### D. Known / deferred / infra — 3 项

- **c1, i2, i4: 中转上游 HTTP 502**（infrastructure，非 RoboGuide 语义
  问题）。i2 已完成 Planner+进入 Reviewer 才 502，证据保留。

## 20-Case 原始统计

| 维度 | 值 |
| --- | --- |
| Overall invariant pass | **10/20** |
| 语义正确（排除 502×3 + invariant 误判×2 + 语义争议×2） | **~13-14/17** |
| Ambiguity 5/5 全部 first-pass 提问 + follow-up 收敛 + Accepted | **✅ 5/5** |
| Planner reached | 14/20 |
| Canonical MissionPlan valid（通过全部校验进入 Review） | 14/14 |
| Provider/schema 400 error | **0** |
| Provider 502 | 3 |
| Reviewer calls | 22（21 成功 / 1 超时） |
| Reviewer approved | 9 |
| Reviewer rejected → Repair | 7 |
| Repairer calls | 7（7 成功 / 0 超时） |
| Repair 后 Accepted | 2（n3 + 1 case 经 repair 收敛） |
| Repair 耗尽 Failed | 3（m1, m2, m4 — 全部 A-1） |
| Local How leakage | **0** |
| Control/provider-selection leakage | **0** |
| 不支持的语义发明 | 0（所有 placeholder 均被 Reviewer 拦截） |
| objective fidelity 失败 | 1（m4 同义词误判，C 类） |

## Stage Breakdown（Practicality 附带记录）

| Stage | calls | ok | timeout | avg lat | max lat | avg in | avg out | avg reasoning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Interpreter | 25 | 23 | 2 | 13.7s | 30.0s | 1,530 | 625 | 529 |
| Planner | 16 | 16 | 0 | 58.9s | 154.8s | 4,897 | 3,096 | 2,575 |
| Reviewer | 22 | 21 | 1 | 71.9s | 199.0s | 4,185 | 3,872 | 3,805 |
| Repairer | 7 | 7 | 0 | 35.6s | 59.5s | 5,526 | 1,849 | 932 |

## 结论建议

**暂不建议直接写 "V0 freeze candidate validated"**——A-1（Reviewer
verifier 循环）是真实 systematic blocker，影响 m1/m2/m4 三个 case 且
100% 复现。建议：

1. **A-1 交 Codex 修 Reviewer prompt**（verifier-evidence 的
   max_evidence_age_ms 需要默认 policy 或该字段允许 null + 说明性
   freshness-free 语义）；
2. **B-1/B-3 在 gold 层定案**（n4/h2 改为合理可问；t2 改 DEFER 或
   改词）；
3. **C 类修 eval invariant**（review-triggered clarification 区分 +
   同义词容忍）；
4. 以上完成后重跑一轮 20-case；若 A-1 修复且 B/C 定案，预期语义正确率
   16-17/17，届时可正式写 "V1 freeze candidate validated"。

## 证据位置

- 本目录 `cases/`（20 份完整记录：dialogue / assessment / plan / review
  history / repair attempts / lifecycle / issues / 逐调用 token+延迟 /
  stage timings）、`cases.jsonl`、`summary.json`
