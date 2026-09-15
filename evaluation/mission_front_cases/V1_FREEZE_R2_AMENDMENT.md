# V1 Freeze R2 Amendment

- Baseline: `main@68cd10374813449b57a013289a4435f24afd4522`（含 ADR-0039
  verifier freshness 共享 policy 修复）
- 前置证据: `evaluation/baselines/mission-front-v1-freeze/
  mission-front-20260915T034439Z-2455d073/`（commit `7de0667` 的 R1
  真实回归）
- 本文件记录 v1-freeze.yaml → v1-freeze-r2.yaml 的五处最小 gold /
  measurement 修正。**历史 v1-freeze.yaml 与 baseline.yaml 不修改**，
  它们分别对应已完成的 R1 回归证据与历史 29-case 基线。

## 修正原则

R2 修正不是"模型没过所以放宽标准"。每一处修正都来自 R1 raw evidence
暴露的以下四类具体问题之一：

1. **missing gold entity identity** — gold 期望不应问，但指令本身缺少
   实体身份，Blocking 是正确行为（gold 错了，不是模型错了）；
2. **missing required canonical parameter provenance** — Catalog required
   参数在指令中无 provenance，Planner 被迫发明或被 Reviewer 正确拦截；
3. **unsupported patrol operation** — Catalog v0.3 无 patrol op，
   Reviewer enforce 该缺口是正确行为（V2 audit 已判定，R1 确认）；
4. **eval measurement false negative** — invariant 测量口径错误，
   不是 production 或模型问题。

## 逐条修正

### 1. n4-edge-inference — missing gold entity identity

- **R1 行为**: Interpreter 追问"请指定要分析的录制激光点云数据（例如
  文件、数据集或录制时间范围）"→ NeedsClarification。
- **判定**: data-source identity 属 materially different target，按
  ADR-0038 Blocking 是正确的；gold 将其标为"不应问"是错的。
- **R2 修正**: 指令改为
  `"使用 pointcloud-analysis-v1 模型对录制文件 lidar-run-017 做一次推理分析"`。
  case 继续只测试 compute.infer（model provenance 来自指令）。

### 2. h2-mapper-then-transport — missing gold entity identity

- **R1 行为**: Interpreter 追问"请明确要运送的那一批样品（例如批次号、
  容器/标签或其他选择标准）"→ NeedsClarification。
- **判定**: "一批样品"缺 identity，Blocking 正确；gold 错。
- **R2 修正**: 指令改为
  `"先给新仓库建图，然后把批次 B-204 的样品从门口运到检验台"`。
  map→relocate 顺序语义不变。

### 3. t1-deadline — missing required parameter provenance

- **R1 行为**: Planner 诚实写 `source=已打印的季度报表当前所在位置`，
  Reviewer 按 2de3171 新规判 placeholder → RequestClarification。
- **判定**: object.relocate@v1 的 `source` 是 required canonical
  parameter；用户指令（"把打印好的…"）未提供 source，该字段无
  provenance。R1 时我们将其记为 B-2 边界问题；R2 决定直接在指令中
  提供 source（`打印室`），本 case 继续只测试 deadline + relocate。
- **R2 修正**: 指令改为
  `"一小时内把打印室里的季度报表送到经理办公室"`。

### 4. t2-start-window — unsupported patrol operation

- **R1 行为**: Reviewer 判 `mobility.navigate` 无法建立"迎宾巡逻开始"
  的 expected effect → RequestClarification。
- **判定**: Catalog v0.3 无 patrol op（V2 audit 已知），Reviewer
  enforce 该缺口是**正确行为**。本 case 原本只测 timing start-offset，
  "迎宾巡逻"引入了不支持的语义。
- **R2 修正**: 指令改为 `"任务接受后 1 小时移动到园区大门"`；
  coverage terms 去掉"巡逻"。保留 timing 测试意图。

### 5. m4-map-relocate-verify — eval measurement false negative

- **R1 行为**: plan objective 写"先为新库房**建立地图**…"，invariant
  的 must_cover_objective 用精确 substring 匹配"建图"→ 误判 objective
  fidelity 失败。
- **判定**: "建立地图"与"建图"同义；这是 measurement 的 false
  negative，不是模型问题。不做通用 NLP/synonym matcher。
- **R2 修正**: 该 case 的 coverage terms 改为语义稳定词
  `["地图", "三箱样品", "上架"]`。

## 6. Clarification invariant 修正（measurement，非 case）

R1 中 t1/t2 的失败发生在 Planner/Reviewer **之后**（review route 为
RequestClarification），但 invariant 将其计入 first-pass over-ask。
修正后 `check_clarification_behavior` 只统计首次 Planner/Reviewer 之前
由 Interpreter 产生的 clarification；review-triggered clarification 单独
记录为 `review_triggered_clarifications`，不参与 first-pass 判定。
附 deterministic regression test。

## R2 未修改的内容

- 其余 15 个 case 的指令与 expectation 原样保留；
- A-1（verifier freshness 死循环）**不在 gold 层修**——它已由
  production 修复（ADR-0039 共享 satisfaction policy，
  `max_evidence_age_ms = 5000`），R2 真实回归负责验证修复生效；
- baseline.yaml / v1-freeze.yaml 不动。
