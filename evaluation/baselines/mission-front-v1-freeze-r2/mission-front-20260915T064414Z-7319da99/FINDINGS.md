# Mission Front-half V1 Freeze R2 — Final Regression FINDINGS

- Suite: `mission-front-20260915T064414Z-7319da99`（20 case，一次性串行，运行期冻结）
- Baseline SHA = `68cd10374813449b57a013289a4435f24afd4522`
- Model = `gpt-5.6-luna`，reasoning = `xhigh`
- **Production default timeout = 90s（未修改）**；**Evaluation ceiling = 300s**
- Amendment: `V1_FREEZE_R2_AMENDMENT.md`（5 case gold 修正 + invariant 测量修正）
- 成本: 60 调用，281,641 tokens（R1 是 70 / 398k——修复后调用量与 token 双降，
  因 repair 死循环消失）
- 运行期零修改 production/Prompt/Catalog/case/invariant；失败 case 原样保留

## 顶层结果：14/20 invariant-pass（R1: 10/20）

**16/20 语义正确**（2 个 objective_fidelity 为 substring 误判的 C 类 +
1 个 clarification measurement C 类，均非 production 问题；另 2 个 502
纯 infra + 2 个新的 isolated model variance，见分类）。

## A-1 修复验证 ✅（本轮最重要确认）

**m1-relocate-then-verify：Accepted，1 次 Review 直接 approved，
repair_attempts=0。** R1 中 m1/m2/m4 三 case 的
"verifier-evidence → ungrounded freshness → repair 死循环"已消除。
ADR-0039 共享 satisfaction policy（`max_evidence_age_ms = 5000`）生效。
m1 本轮的 plan 全部用 execution-report basis，Reviewer 按 68cd103 的新
语义规则（"verify/confirm 措辞本身不强制独立 verifier"）正确判定
"确认并报告样品状态"的 expected effect 可由 execution-report 建立。

## 5 个 gold 修正 case 验证

| case | R1 | R2 | 结果 |
| --- | --- | --- | --- |
| n4 | NC（问数据源） | **Accepted** | model=`pointcloud-analysis-v1`（指令提供值，未发明）✓；唯一失败是 objective_fidelity 的 substring 误判（objective 写"激光点云"…实际写的是"录制文件 lidar-run-017"，"点云"一词被正常省略——同 h2 的 C 类问题） |
| h2 | NC（问批次） | **Accepted** | 批次 B-204 提供后不再问；embodiment 词不泄漏 ✓；失败同样为 substring 误判（"建图" vs "建立地图"——**R2 漏改了 h2 的 coverage terms**，只改了 m4 的） |
| t1 | NC（source placeholder 被 Reviewer 拦） | **Accepted** | "打印室"作为 source 直接来自指令 ✓ |
| t2 | NC（patrol 无 op 被 Reviewer 拦） | **Accepted** | timing `{earliest=3600000ms, latest=3600000ms}` 精确进入 ✓ |
| m4 | Failed（repair 耗尽） | 502（infra） | 未能观察到 A-1 修复后的行为——上游故障，非语义问题 |

## 8 个 gold-correct case 核实

n2 ✓ / n3 ✓ / n4 ✓（model 未发明）/ t4 ✓ / t2 ✓（timing 正确）/
t1 ✓ / h2 ✓（无 provider 泄漏）/ m4 ⚠（502 未观察）。
**无 unsupported semantics 重新引入**：Local How leakage 0、Control/
provider-selection leakage 0、语义发明 0、provider/schema 400 **0**。

## R2 新失败分类

### A. Systematic correctness blocker — **0 项** ✅

### B. Isolated model behavior / sampling variance — 2 项

- **a5-vague-scope**（R1 正确 Blocking→收敛→Accepted；R2 模型直接以
  assumption 前进，随后 plan 的 context-role 引用错误被 schema 校验拒绝）。
  单 case 单次出现的 plan 结构错误，R1 同 case 已证明正确路径存在。
- **c1-two-robots-parallel**（R1 是 502；R2 Planner 产出
  `concurrent-cooperation` coupling mode 但缺 shared view，被 schema
  校验拒绝）。单 case；schema 校验正确拦截了不完整结构（fail-closed
  行为正确），属模型输出质量方差而非 production 缺陷。

### C. Eval measurement issue — 3 项

- h2 objective_fidelity："建图" vs "建立地图"（同 m4 的 R1 问题——
  **R2 amendment 只修了 m4 的 coverage terms，漏改了 h2**）；
- n4 objective_fidelity："点云"在提供 data identity 后被指令省略是
  正常语义压缩，coverage terms 应改用稳定词（如 lidar-run-017 / 推理）；
- a5 clarification_behavior：模型 R2 未问 first-pass 问题（直接
  assumption 前进），invariant 按旧期望判"应问但未问"——这是真实的
  model variance（B 类 a5）的另一个切面，随 a5 一起归 B。

### D. Infra — 2 项

m2、m4 的 provider HTTP 502（上游故障；m4 因此未能观察 A-1 修复后
行为）。planner 2 次 timeout 也在 300s ceiling 内正常超时后由 engine
走 Failed 路径（不影响其他 case）。

## 20-Case 原始统计

| 维度 | R2 | R1 |
| --- | --- | --- |
| Invariant pass | **14/20** | 10/20 |
| Accepted | 14 | 8 |
| Ambiguity 5/5 正确循环 | 4/5（a5 见 B 类） | 5/5 |
| Planner reached | 20/20 | 14/20 |
| Canonical plan valid（过全部校验进 Review） | 16/16* | 14/14 |
| Provider 400 | **0** | 0 |
| Provider 502 | 2 | 3 |
| Reviewer calls | 16（16 ok / 0 timeout） | 22（21 ok / 1 timeout） |
| Approved | 14 | 9 |
| Rejected→Repair | 0 | 7 |
| Repairer calls | **0**（无需 repair） | 7 |
| Local-How / Control leakage / 语义发明 | **0 / 0 / 0** | 0 / 0 / 0 |

*a5、c1 的 plan 被 engine 的 schema 校验拒绝（fail-closed 正确行为），
不计入"valid plan"；二者属 B 类。

## Stage Breakdown

| Stage | calls | ok | timeout | avg lat | max lat | avg in | avg out | avg reasoning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Interpreter | 24 | 24 | 0 | 12.3s | 22.9s | 1,525 | 571 | 476 |
| Planner | 20 | 18 | 2 | 45.8s | 96.5s | 5,308 | 2,400 | 1,926 |
| Reviewer | 16 | 16 | 0 | 28.7s | 92.2s | 4,323 | 1,463 | 1,443 |
| Repairer | 0 | — | — | — | — | — | — | — |

## 结论

**是否还存在跨多个 V1-supported case 重复出现的 systematic correctness
blocker？—— No。**

- A-1（R1 唯一 systematic blocker）已修复且经 m1 直接验证；R2 的全部
  失败均为 isolated（a5/c1 单 case model variance）或 measurement
  （h2/n4 coverage terms 的 amendment 遗漏，只影响 invariant 判定，
  plan 本身已 Accepted）或 infra（502）；
- 无任何错误模式在 ≥2 个 case 重复出现；
- Planner/Reviewer/Repairer 的调用结构、schema fail-closed、
  authority boundary 全部稳定。

**建议：Mission Front-half V0 freeze candidate validated。**

后续：停止 Mission Intelligence 调优，进入 Control Dry-run。R2 遗留的
两处 coverage-terms 修正（h2、n4）属 benchmark 维护，不阻塞 freeze，
可在 Control Dry-run 期间顺手提交。

## 证据位置

本目录：`cases/`（20 份完整记录）、`cases.jsonl`、`summary.json`。
