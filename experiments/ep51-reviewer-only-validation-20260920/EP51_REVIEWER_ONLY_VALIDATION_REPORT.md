# Episode51 新版 Reviewer 真实模型语义审查报告

日期：2026-09-20。实验目录：
`/data/workspace/code/roboguide-ep51-reviewer-only-20260920T144003Z`。

性质：Reviewer-only、两次固定调用的诊断性验证。不是完整 Mission Request，不是正式 E1
benchmark，没有调用 Planner、Repairer、Mission Service、Control、Node 或 Habitat。

## 1. 结论

本轮预注册的三个验证目标全部通过：

1. 新 Reviewer **识别了 A3 无原始任务依据的 `requires-active`**。它返回
   `approved=false`，在 `/contexts/0/relations/0` 指出：联合终态不会因为一个 move 已完成、
   另一个仍在运行而失败，因此该 relation 增加了无依据的生命周期限制。该 issue 的
   `required_action=RepairPlan`。
2. 新 Reviewer **批准了 B1 independent 计划**：`approved=true, issues=[]`。它没有要求
   concurrent-cooperation、shared view、requires-active、State export、peer channel、
   distinct physical executors 或额外目标。
3. 两个输出都能被 `MissionPlanReview.from_json` 解析。A3 路由为 `Repair`，B1 路由为
   `Approved`。现有 Request Engine 对前者会进入受限 Repairer 路径，对后者会进入
   `_advance_admitted_draft`；本实验没有执行这两个后续动作。

这是单次 A3、单次 B1 的有限样本，能证明本次冻结输入上的实际表现，不能据此估计 Reviewer
的稳定成功率或所有协作任务上的泛化能力。

## 2. 代码、Prompt 与历史证据身份

| 项目 | 实际值 |
|---|---|
| Reviewer 代码分支 | `codex/mi-prompt-refinement` |
| 实际代码 SHA | `28a3dcda5eb44d48df83700d13b4e0b1b601ea97` |
| 历史 A/B 报告分支 | `codex/ep51-mi-prompt-ab-report` |
| 历史证据 SHA | `322bb8d39f839ff095a1df086e66b105dea307a4` |
| Reviewer Prompt | `/tmp/roboguide-mi-prompt-refinement/mission/prompts/v0/reviewer.md` |
| Prompt 文件 SHA256 | `daa7238177c93aa54c903cc4411d3c0d041009fa7644cb987d88bb4007956c1e` |
| 实际发送 Prompt SHA256 | `f84caceb5c9f6d33e3a00df987a95920b7f7655fde8d5f205ca9e6cb39a93f63` |

文件 SHA 与发送 SHA 的差异来自生产 `_load_prompt()` 的 `.strip()`，不是实验改写。
两组实际请求分别保存了完全相同的 [A3 Prompt](A3/prompt.txt) 与
[B1 Prompt](B1/prompt.txt)。

历史来源为上一轮分支中的：

- `experiments/ep51-mi-prompt-ab-20260920/A/state/final-plan.json`
- `experiments/ep51-mi-prompt-ab-20260920/B/state/final-plan.json`
- `experiments/ep51-mi-prompt-ab-20260920/frozen/base-input.json`
- `experiments/ep51-mi-prompt-ab-20260920/manifest.json`
- `experiments/ep51-mi-prompt-ab-20260920/EP51_MI_PROMPT_AB_REPORT.md`

A3 final plan 是上一轮 A 组第 3 次真实模型响应通过生产 normalizer 和完整校验后的 canonical
MissionPlan；与 `A/evidence/attempt-03/normalized-draft.json` 的唯一差异，是 canonical
`MissionPlan.to_json()` 删除了 `shared_view.spatial_reference=null`。B1 final plan 与 B 组第 1 次
normalized draft 逐对象相等。本轮没有重新生成、人工编辑或修补两份计划。

本轮副本及文件摘要：

| 计划 | SHA256 | 历史摘要 |
|---|---|---|
| [A3](A3/historical-plan.json) | `fe756acd49febc1bf6b30354a8221d9cd67bc029d1116ee9fe442de99a4967b5` | concurrent-cooperation；1 条 requires-active；execution-only shared view；2 Tasks；均无 DAG 依赖；各 `space:1` |
| [B1](B1/historical-plan.json) | `0e32e4e530f1a255a0ae6398dec0b23aa8a2dc9ac7e78ef64440a00c95da8bcf` | independent；relations=[]；无 shared view；2 Tasks；均无 DAG 依赖；各 `space:1` |

两份计划的 mission_id 均为
`mission-deba13bb19d6422db398b1cb916f6462`，并保留两个目的地：
`any_targets|0` 与 `TARGET_any_targets|0`。

## 3. 冻结 Reviewer 输入与配置一致性

实际输入来自生产 `ResponsesMissionReviewer.review()`，没有向 Provider 添加人工判断、预期标签、
validation error 或提示性文字。公共字段逐对象相等：

- `grounded_intent`
- `grounding_context`
- `capability_catalog`
- `satisfaction_policy`
- `deployment_execution_profile`
- `authoritative_semantic_goal`

两组唯一不同的 input 字段是 `mission_plan`。公共输入摘要为
`abb2af960f073ba28e5b21916b2b4765cf97a1dc5bf775cf0b21282498a80cf9`。
完整脱敏输入见 [A3 input](A3/input.json) 和 [B1 input](B1/input.json)；程序复核结果为
`public_common_equal=true`、`request_config_equal=true`。

权威语义目标保持：

```text
and(any_at(any_targets|0), any_at(TARGET_any_targets|0))
objective_scope = joint_terminal_state
```

GroundedIntent 说明两个 reach 条件可以由同一或不同机器人满足，并且不需要 transport、handoff
或额外 confirmation；没有要求某个 navigation execution 在另一个 execution 期间保持 Active。

Provider 请求配置：

| 配置 | A3 / B1 |
|---|---|
| endpoint | `http://101.43.45.215:8080/responses` |
| wire API | Responses |
| model | `gpt-5.6-luna` |
| reasoning effort | `xhigh` |
| max output tokens | `4096` |
| response storage | disabled |
| timeout | 600 seconds |
| structured output | strict `mission_review_v0` JSON Schema |

两次响应都报告 `model=gpt-5.6-luna`、`status=completed`。模型 alias 一致不证明服务端不可变
模型版本一致，因为响应没有提供可独立验证的 backend revision/system fingerprint。

请求证据在网络调用前已经保存；Authorization 值已脱敏。敏感模式扫描未在实验目录发现密钥
或 Bearer credential。

## 4. A3 真实 Review 结果

原始结构化输出见 [A3 provider review](A3/provider-review.json)，完整响应见
[A3 provider response](A3/provider-response.json)。

结果：`approved=false`，5 个 issue，全部为 `RepairPlan`：

| code | path | Reviewer message |
|---|---|---|
| `unjustified-concurrent-cooperation` | `/contexts/0/coupling_mode` | 两个 terminal reach predicates 不要求执行期协作；多个机器人和共享场景不能建立这种依赖，应使用 independent |
| `unjustified-requires-active` | `/contexts/0/relations/0` | 一个 move 已完成而另一个仍在运行不会破坏联合终态；relation 增加了无依据的 lifecycle restriction，应删除，而非用它表达普通并行 |
| `unnecessary-shared-view` | `/contexts/0/shared_view` | execution-only view 没有语义消费者，不能反向证明 cooperation mode/relation 合理 |
| `unjustified-task-cooperation` | `/tasks/0/coupling_mode` | 第一个 reach outcome 无执行期依赖；资源允许时可独立并行，Task mode 不应编码 cooperation |
| `unjustified-task-cooperation` | `/tasks/1/coupling_mode` | 第二个 reach outcome 同样无执行期依赖，应与 independent Context 一致 |

人工证据复核：这 5 个 issue 都由冻结输入和 A3 结构支持。核心 issue 精确命中目标路径，并正确
理解 `requires-active` 的限制：source execution 不能在 target 仍运行时先成为 terminal。原联合
终态只要求两个 `any_at` 最终同时成立，不要求两个 execution 同时 Running，也没有声明持续
观察、交接或共享状态消费。

Reviewer 没有要求增加 State export、peer channel、不同 Physical Entity、额外导航目标或其他
无依据机制。`RepairPlan` 合理，因为冻结输入已经足以将 mode/view/relation 修正为与任务语义
一致的结构，不需要用户补充语义，也不是部署 State contract 缺口。

调用耗时 88.93 秒。Provider 报告 input 6,838、output 4,756、其中 reasoning 4,367、总计
11,594 tokens。Provider 报告的 output token 数高于请求的 `max_output_tokens=4096`；同一代理在
上一轮也存在类似回显/usage 能力边界，本报告如实记录，不能由客户端证据解释其计量语义。

## 5. B1 真实 Review 结果

原始结构化输出见 [B1 provider review](B1/provider-review.json)，完整响应见
[B1 provider response](B1/provider-response.json)。

结果：

```json
{"approved": true, "issues": []}
```

人工证据复核：B1 保留同一 Mission 的两个官方终态谓词、两个 navigation Task、两个
`space:1` 资源需求及空 DAG；`independent` 只说明没有必须声明的执行期合作依赖，不要求串行，
Control 仍可在资源可用时并行调度。Reviewer 没有把联合终态误读为持续并发协作，没有要求
shared view/relation，也没有把两个逻辑 Actor 强制绑定为不同 Physical Entity。本轮未发现它
提出其他有依据或无依据的问题。

由于 Review 契约规定批准时 `issues=[]`，该输出没有自然语言批准理由；因此能确认的是它在此
冻结样本上作出了正确的批准行为，不能从空 issue 反推出模型内部推理过程。

调用耗时 93.75 秒。Provider 报告 input 6,715、output 5,074、其中 reasoning 5,054、总计
11,789 tokens。同样存在 output usage 高于请求上限的回显异常，未影响结构化输出解析。

## 6. 确定性校验与 Review 路由

零 Provider 调用重放了当前完整 MI 确定性校验：A3 和 B1 都 PASS，各有两个 Task。见
[deterministic-contract-check.json](deterministic-contract-check.json)。这再次说明：A3 的关系
在 schema、endpoint、实现支持和 Catalog 层都合法；结构合法并不能证明关系有任务语义依据。

两份真实 Review 输出经过 `MissionPlanReview.from_json` 均 PASS：

| 计划 | Review | `route_mission_review` | Request Engine 后续路径 |
|---|---|---|---|
| A3 | 5 × RepairPlan | `Repair` | 在 repair budget 与 Repairer 可用时进入 `MissionRequestLifecycle.REPAIRING`，调用 Repairer；不会直接提交 Control |
| B1 | Approved | `Approved` | 进入 `_advance_admitted_draft`，再执行 approval policy；本轮没有继续到审批或 Controller submission |

源码依据：
`/tmp/roboguide-mi-prompt-refinement/mission/src/mission/request_engine.py` 的
`_review_and_advance()`：510–564；`_advance_admitted_draft()`：585–601；`_submit()`：603–616。
离线结果见 [offline-route-check.json](offline-route-check.json)，其 Provider 调用数为 0。

Review JSON 本身不含 plan digest。生产 Mission Request 会通过 `MissionPlanReviewAttempt` 将
review 绑定到 draft revision/digest/context digest；本组件实验没有创建 Request record。这里的
可复核绑定来自：每个实际 Provider request 内包含完整 plan，本实验 manifest 和 summary 保存其
SHA256，并把 response 存在同一 arm 目录。不能把这种实验归档绑定宣称为生产 Review history。

## 7. 调用预算、证据与未验证事项

- 实际 Provider 调用：2 次；A3=1，B1=1；顺序 A3 → B1。
- 重试：0；Planner/Repairer/regenerate/其他模型调用：0。
- Provider/身份/配置错误：无。
- Mission Request、Controller submission、物理执行：均未发生。
- 本轮没有修改 Prompt、源码或历史 A/B 证据；原工作区未切换、未清理。

完整清单及配置：[manifest.json](manifest.json)；执行结果：[execution-summary.json](execution-summary.json)。

尚未验证：

1. 新 Reviewer 在重复采样、其他模型版本和更多任务上的稳定性。
2. 真正需要 concurrent/tightly-coupled cooperation 的真实模型 Reviewer 行为；之前只有离线
   fixture 保护，本轮没有扩展 Provider 预算。
3. A3 进入真实 Repairer 后能否一次生成忠实且合法的 independent replacement；本轮明确禁止
   调用 Repairer。
4. B1 经完整 Request Engine approval/admission、Control 与物理执行后的结果。
5. Provider alias 背后的不可变模型版本及 token usage 超限回显的具体服务端含义。

建议下一步由人工先审查本报告和完整 evidence。若要验证恢复闭环，应另行预注册一个
Reviewer→Repairer 的单次诊断，固定 A3 review 与输入，并单独限制 Repairer 调用预算；不要把
本次 B1 approved 计划直接注入完整链路或作为正式 benchmark 结果。
