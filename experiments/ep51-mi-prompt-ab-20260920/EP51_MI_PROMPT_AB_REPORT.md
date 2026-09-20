# Episode51 MI Planner Prompt A/B 真实模型对照报告

> 发布说明：本报告及证据现按用户后续指令发布到独立报告分支。实验阶段的“不提交或推送”描述保留为当时记录；未修改实验结论或原始模型数据。机器本地的执行/分析脚本不随报告发布，见 [README](README.md)。

日期：2026-09-20。实验目录：`/data/workspace/code/roboguide-ep51-mi-prompt-ab-20260920T124627Z`。
性质：**重建输入下的 MI-only 诊断性对照，不是正式 E1 benchmark，不是完整 Mission Request 或物理执行。**
调用时间（UTC）：2026-09-20T12:50:47.971142+00:00 → 2026-09-20T13:01:13.191278+00:00。

## 1. 主要结论

1. **旧 Prompt 本次再次先生成非法 concurrent-cooperation；新 Prompt 首次生成合法 independent。**
   新组保留两个任务、两个官方目标引用、两个 `space:1` 要求、无 DAG 依赖，没有额外 relation、State export 或 distinct-executor 约束。
2. **旧组恢复表现暴露了“结构通过但语义加码”的风险。** A1 缺 view；A2 只增加 execution-only view，仍缺 relation；
   A3 只增加 `requires-active`，通过完整 MI 校验。这个新增依赖要求一个导航 execution 在另一个执行期间持续 Active，
   冻结任务没有这种要求，不能将 A3 作为语义忠实成功。
3. **新 Prompt 在本次固定输入下的初次生成表现更好；其真实 regenerate 改善尚未验证。**
   B1 首次通过后即按预注册停止，没有制造错误、额外重试或追加合作任务的真实模型调用。
4. 这仍不能解释历史样本变化的唯一因果：旧 Prompt 曾成功，新 Prompt 此次成功，不等于能排除 Interpreter 措辞、
   snapshot 元数据、模型随机性、服务端路由/版本、时间/缓存等差异。不能以一组诊断估计成功率。

总调用数 **4**：A=1 initial+2 regenerate；B=1 initial+0 regenerate；协作保护检查=0 Provider 调用。
没有启动 Control、Node、Habitat、Mission Service 或 B1 Runner，没有提交正式 Mission Request，没有修改源码、Prompt 或历史证据。
没有 Reviewer/Repairer 模型调用；“完整 MI 校验”指既有 Planner 的确定性完整校验链，不等同于经过 production Reviewer 的完整 MI 流程。

## 2. Git、Prompt 与配置身份

| 项目 | 确认结果 |
|---|---|
| 远端 main（git ls-remote + fetch 后 origin/main） | `6623799da53870445db7383db6dfb77c683b4c68` |
| 原工作区 | `/data/workspace/code/RoboGuide`，`codex/b1-event-archival@ad02d5624753614696030e244a36335cfa7ccf47`，未切换或覆盖 |
| 实验实际加载的现有干净代码视图 | `/tmp/roboguide-mi-coordination@6623799da53870445db7383db6dfb77c683b4c68` |
| A 旧 Prompt commit | `02f164b8831be873eedd923bc28d737301b8453e` |
| B 新 Prompt commit | `6623799da53870445db7383db6dfb77c683b4c68` |
| A 文件 SHA256 | `a1d02bf81597412e59bd91f629ad8c6263fc69e1665fae3d522323bd20b86f92` |
| B 文件 SHA256 | `14dc683c40ae46dd64101d0ff9d0d502f3f21688bbe79ef43df6c406e77e67cc` |
| A 实际 instructions SHA256 | `ac9ba1b3dc5fbefa2f1ebee126a62fb35d5b010b3100212134acbed0d2a9ce76` |
| B 实际 instructions SHA256 | `21a4688d2789a3400a4b2badc0f3ec826a919cf2096cd2ac98d674168dc237e5` |

实际 instructions 与文件摘要不同，是既有 `_load_prompt()` 使用 `.strip()` 去除边缘空白；不是实验修改 Prompt。
逐字文件保存在 [旧 Prompt](frozen/planner-A.md)、[新 Prompt](frozen/planner-B.md)；每次实际发送的 `prompt.txt` 单独保存。

新 Prompt 在原文后追加 49 行通用指导：independent 可并行；模式由真实执行依赖决定；concurrent 需要合法 view+relation；
紧密协作保留 peer channel 等机制；execution 不需要 export，pose/velocity 需要明确来源；恢复应回到真实任务需求，
不能机械补字段、编造关系或降低真实需求。未更改旧段落。

两个提交之间 `mission/src/`、`config/`、`contracts/`、execution profile 的 Git diff 为空。
此次只使用两个 Planner Prompt 的差异；没有调用 Interpreter 或 Repairer，因此修复提交中 Repairer Prompt 的变化不进入本次真实调用。

## 3. 冻结输入：来源、重建与一致性

原始来源：

- `/data/workspace/code/roboguide-ep51-diag2-20260920/b1-diag-ep51-seed40/b1-request-record.json`：
  `mission_id`、`assessment.objective/constraints/assumptions`、完整 `grounding_context`。
- 同目录 `b1-request-observations.json`：三次历史 raw/normalized output、validation errors、provider identity；用于只读预检与回放核对。
- 同目录 `mission-service-b1.toml`：execution profile 路径；不是完整 MI 模型配置正文。
- Git OLD/NEW 中完全相同的 `config/mission.toml`、capability v0.3 catalog、execution profile、MissionPlan v0.8 schema：
  重建未在历史请求中单独快照的输入资产。

**历史完整 Provider HTTP 请求未保存，本轮是“重建的 MI-only 输入”，不声称与历史 wire body 逐字节相同。**
没有重新运行 Interpreter，直接复用 D 的真实 assessment。request_id 仍只作为归档 Grounding Context 身份存在，
未据此创建或提交新的 Mission Request。

- mission_id：`mission-deba13bb19d6422db398b1cb916f6462`。
- grounding context digest：`sha256:992f5eca78d43d3d398ba0bd2db24714448d35df898e7772d1436e8d7f235478`。
- authoritative semantic evidence digest：`sha256:efad4d24f94a6ce0f2464c4da7a8307b2b0d3262f5998746060e521974cc185b`。
- base-input JSON SHA256：`bc9a073e88a21d6898882200eb940e954ef3ffd2f6415c431fc479f492456cd9`。
- 除 instructions 外请求公共部分摘要：`de70cb4fb6a5346db1256047531a4218dda35a1ff5b64d9b33ec9cbcc5e8e342`。

完整输入：[base-input.json](frozen/base-input.json)。它包含真实 grounded intent、冻结 context、Catalog、profile、policy、
以及由当前既有适配器构建的 `authoritative_semantic_goal`。后者保留：

```text
and(any_at(any_targets|0), any_at(TARGET_any_targets|0))
objective_scope = joint_terminal_state
```

`do_not_split_joint_objective_into_independent_missions=true` 同样保留；它不要求单个 Mission 的 Context 采用 concurrent 模式。
源代码依据：`/tmp/roboguide-mi-coordination/mission/src/mission/semantic_evidence.py:264–279`。

两组初始实际 payload 已在任何网络调用前由同一 production adapter 经 CaptureTransport 构造；去掉 instructions 后逐对象相等。
每次真实发送前再次检查：URL、timeout、model、schema、全部 base input 必须与该冻结请求相同。
重生成只额外携带上一轮 `provider_output` 和 `validation_errors`；逐次核对通过。
A2 和 A3 的 feedback 分别等于 A1 和 A2 的完整原始输出及当轮错误，不是事后整理的简版。
见 [offline-evidence-analysis.json](offline-evidence-analysis.json)。

## 4. Provider、预算与可核对边界

| 参数 | 两组实际发送值 |
|---|---|
| endpoint | `http://101.43.45.215:8080/responses` |
| provider 配置名 / wire_api | `OpenAI` / `responses`，既有 deployment relay |
| model | `gpt-5.6-luna` |
| reasoning.effort | `xhigh` |
| max_output_tokens | `4096` |
| store | `false` |
| structured output | `json_schema`，`mission_plan_v0`，`strict=true`，完整 schema 已冻结 |
| timeout | 600 秒/次 |
| 每组预算 | 1 initial；仅 RejectedPlanError 允许最多 2 次 regenerate；无网络错误重试 |
| 顺序 | A → B；成功即停止；不追加样本 |

`temperature`、`top_p`、模型随机 seed、`service_tier` 没有额外加入请求，沿用原适配器行为。
Episode 的 Habitat seed40 是原 workload 身份，不是模型采样种子，本轮不运行 simulator。

当前 shell 起初未设置 `OPENAI_API_KEY`。已核对历史 `launch.py` 的装载方式，使用它曾使用的同一用户授权来源，
仅在内存传给现有 MI transport。未读取其他服务凭据、未更换模型或服务商。沿用既有 Runner 的远端 HTTP 显式 opt-in；
没有输出、保存凭据值、前后缀、摘要或 Authorization header。Proxy 环境的非敏感部分见
[transport-environment.json](transport-environment.json)。

四次响应均报告 `model=gpt-5.6-luna`、`status=completed`，未发生 HTTP、鉴权、超时或模型身份不符。
每次 response id、完整 response、usage 均保存；没有不可变后端版本或 `system_fingerprint`。
因此仅核对了请求配置与响应 model alias，不能证明 relay 背后的权重版本完全相同。

**Provider 参数回显存在观测限制：**四次 response 的 `max_output_tokens=null`，报告的 A1/A2/A3 output_tokens
分别为 5723/5683/6885，超过请求 4096；B1 为3971。四次回显的 temperature=1.0、top_p=0.98、store=false、
reasoning.effort=xhigh 一致。实际发送的上限均为4096且未经改动，但服务端如何解释/执行上限尚不能确认。
此项不能伪报为服务端上限已验证，亦不应用这些数值作严格预算或性能比较。
这不是已观察到的 A/B 请求参数差异；客户端配置一致性通过，不代表已证明全部服务器内部配置一致。

## 5. 每次真实调用及校验结果

| 尝试 | 类型 | 耗时 | Context mode | relation 数 | 校验结果 | 证据 |
|---|---|---:|---|---:|---|---|
| A1 | initial | 207.71 s | concurrent-cooperation | 0 | REJECTED：contexts[0] mode concurrent-cooperation requires a Group shared view | [A/evidence/attempt-01](A/evidence/attempt-01/attempt-result.json) |
| A2 | regenerate | 104.29 s | concurrent-cooperation | 0 | REJECTED：contexts[0] mode concurrent-cooperation requires an execution relation | [A/evidence/attempt-02](A/evidence/attempt-02/attempt-result.json) |
| A3 | regenerate | 239.75 s | concurrent-cooperation | 1 | PASS：完整 MI 校验通过 | [A/evidence/attempt-03](A/evidence/attempt-03/attempt-result.json) |
| B1 | initial | 73.47 s | independent | 0 | PASS：完整 MI 校验通过 | [B/evidence/attempt-01](B/evidence/attempt-01/attempt-result.json) |

失败按既有 fail-fast 校验报告首个错误；未将后续未执行检查标记为通过。
A3/B1 经过归一化、MissionPlan 结构/语义契约、execution profile、implementation support、physical grounding、
mission_id/objective、Catalog、satisfaction policy 全链校验。
源代码：`/tmp/roboguide-mi-coordination/mission/src/mission/responses.py:81–102`。
全部输出在实验结束后离线重放，校验结果、normalized 草案和最终计划逐对象相符，未追加 Provider 调用。

### A1：重现缺 view

Context 与两个 Tasks 均为 concurrent-cooperation，relations 为空，raw `shared_view=null`。
归一化去掉 optional null，抛出 `contexts[0] mode concurrent-cooperation requires a Group shared view`。
与历史 D 相比，历史 Task 自身是 independent；本次 Task 也选了 concurrent，不能说草案全文与历史相同。
两目标、resources、mission objective 均保留。

### A2：只补共享执行状态

归一化后相对 A1 **唯一变动路径是 `/contexts/0/shared_view`**：
两条 execution binding、include_freshness=false、无 State export。没有 pose/velocity 或伪造 export。
这种 view 自身合法，但 relations 仍为空，故被 `requires an execution relation` 拒绝。
mode、tasks、目标、资源、DAG 均未变；本轮没有根据真实目标重新选择模式。

### A3：结构通过，新增无依据的执行依赖

归一化后相对 A2 **唯一变动路径是 `/contexts/0/relations`**：

```json
{
  "id": "reach-tasks-concurrent",
  "kind": "requires-active",
  "source": {"task_id": "reach-any-targets", "role_id": "reach-any-targets-role"},
  "target": {"task_id": "reach-target-any-targets", "role_id": "reach-target-any-targets-role"}
}
```

这并不是“两个目标可以并行”的中性标记。当前 Runtime 在 target Accepted/Running 时，
source 为 Accepted/Running 才判 Satisfied，source Completed/Failed/Cancelled 则判 Violated。
源码：`/tmp/roboguide-mi-coordination/core/runtime/src/relation/manager.rs:317–351`；原始诊断文本在437–438行：
`required source execution is terminal while target remains active`。

冻结 authoritative goal 和 grounded intent 只要求联合到达终态，没有“第一个导航任务不能先完成”的要求。
所以 A3 添加了输入没有支持的持续活跃约束；一个机器人先到达并报告 Completed、另一个继续到达，
本可以与目标相容，却会违反这个新增 relation。此处是源码支持的反例分析，**本轮未运行 Runtime 验证或物理实验**。
旧组最终结果应分别记为：确定性 MI 校验 PASS；目标覆盖保留；协作语义忠实性不通过本次人工证据审查。
生产 Reviewer 未调用，不能断言它会接受或拒绝 A3。

### B1：一次生成 independent

Context `shared_scene` 与两个 Tasks 均为 independent；`relations=[]`、`executor_constraints=[]`，没有 shared_view/peer_channel。
Task `reach_any_targets_0` 和 `reach_TARGET_any_targets_0` 的 `depends_on=[]`，不会由计划增加串行先后关系。
两者分别使用 mobility.move@v1 和精确 destination：any_targets|0、TARGET_any_targets|0；各自 `space:1`、task scope。
未指定具体 Node、ResourceId、physical_entity 或 distinct-physical-entities。

完整 Mission objective 仍是同一个联合终态。两个 description 明确各自贡献联合终态的一个 any_at predicate。
这与历史合法 independent 组织方式相容；本次检查未发现目标减少、遗漏、额外动作、关系编造或协作降级。
不过目标引用齐全不等于物理终态已经实现，也不等于模型未来一定遵循同样规则。

## 6. 结构、目标覆盖与语义忠实性分开判定

| 项目 | A 最终草案 | B 最终草案 |
|---|---|---|
| 完整 MI 确定性校验 | PASS（第3次） | PASS（第1次） |
| 同一 Mission 联合 objective | 保留 | 保留 |
| 两个任务 / 两目标引用 | 保留 / 保留 | 保留 / 保留 |
| DAG 依赖 | 两个均[] | 两个均[] |
| resources | 两个均space:1 | 两个均space:1 |
| 减少 Task、删目标 | 未观察到 | 未观察到 |
| State export 编造 | 无 | 无 |
| 无依据的 execution relation | 有：requires-active | 无 |
| 本次人工语义审查 | 新增约束无输入依据 | 未发现偏离冻结任务需求 |
| 生产 Reviewer 结果 | 未运行，不可用 | 未运行，不可用 |
| 官方 PDDL outcome | 不适用，未执行Habitat | 不适用，未执行Habitat |

原始模型输出已经包含 resource minima，实验脚本没有事后补丁；A3 的 canonical 输出仅去掉
`shared_view.spatial_reference=null` 的空表示，B1 normalized 与 final 完全相等。
既有 execution profile.apply 仍被正常调用；该行为来自 production adapter，不是实验改写。

## 7. 通用协作语义保护检查

运行现有 `test_coordination_guidance.py` 与 `test_coordination_history.py`：**30 passed，0.62秒，0 Provider调用**。
日志：[offline-protection-tests.log](offline-protection-tests.log)。

重点复用了真实具有执行期依赖的现有 synthetic contract fixture：
`Infer a result while the safety observer remains active.`
Context concurrent-cooperation；`watch → infer` 的 requires-active；共享 watch 的 execution 状态。
这类任务实际需要观察者持续工作，与两个独立到达终态不同。
本次保存了原 fixture 和离线结果：[offline-cooperation-protection.json](offline-cooperation-protection.json)。

测试确认此计划保持并通过实现支持/Catalog检查；Planner初次/重生成/Repairer实际发送相同通用指导；
缺 view/relation、缺必要状态契约、缺 tight peer channel 等仍拒绝，且没有自动强制改成 independent。
这是确定性 fixture 和 Prompt 交付保护，**不是新 Prompt 在真正协作任务上的真实模型遵循证明**。
未为该检查额外调用模型；未来若需真实模型协作样本，必须另行批准输入及预算。

## 8. 为什么“之前可以，现在不可以”

历史报告与原始输入再次核对的事实：

- 历史两份实际被 Control 接受的计划都是 independent，无 shared_view、relations=[]；不是旧版曾放行非法 concurrent。
- 最近第一次失败只有错误记录，没有原始 rejected DTO，不能补造它的详细内容。
- 第二次失败的三份原始 DTO 都为 Context concurrent，relations=[]；缺 view → 非法 pose view → 缺 view。
- 同一历史合法计划在当前完整校验链继续通过，未发现使其失效的校验规则回归。
- A/B/C/D 的实际 Interpreter assessment 文本不同；完整历史 Provider请求、不可变模型版本缺失。

对应证据：原审计报告和本次 [historical-precheck.json](historical-precheck.json)、
[frozen/historical-rejected-drafts.json](frozen/historical-rejected-drafts.json)、已核对的仓库历史 fixture。

本次更明确地证明：在固定 D 的重建输入下，旧 Prompt 仍可选 concurrent，并在恢复中逐条补机制；
这与旧指导没有充分区分“并行”“执行期合作”的缺口相容。
新 Prompt 此次直接选 independent，无需恢复，支持该指导对**本次初次生成**有效。
但无法追溯模型历史内部决策原因，也不能唯一归因于 Prompt 或随机性；原始输入、时间与服务端状态并未跨历史完全冻结。

## 9. 尚未验证的事项与下一步条件

- **新 Prompt 的真实 regenerate 改善：未验证。**本轮没有触发 B 恢复，不能以 mock 覆盖替代真实证据。
- 单个固定顺序 A→B 对照不足以估计成功率、消除顺序/缓存效应或确定唯一因果因素。
- 响应 alias 一致不证明不可变模型后端一致；Provider token上限回显/usage问题仍未解释。
- 结构校验不会证明 relation 必须来自真实任务语义；A3 正是确定性校验与语义忠实性不同的具体反例。
- 本轮不含生产 Interpreter/Reviewer、Control 接收、调度、Node或物理执行结果。

**进入下一次完整链路诊断：从本次 Planner 输出看已具备条件，可在用户审查后按既有协议另行安排一次受控诊断。**
这不是对完整链路通过的保证，也不授权复用/注入本次计划、绕过 production MI 或立即启动。
后续真实运行应保留完整输入/响应与 Reviewer 证据，尤其检查是否出现无依据 relation，而不能只看 schema PASS。
本轮到报告为止，不启动任何下一阶段，不提交或推送。

## 10. 证据目录与复核方法

- [manifest.json](manifest.json)：预注册顺序、预算、配置、输入来源与所有 frozen 文件摘要。
- `study.py`（仅原始本机目录保存）：实验专用脚本，只在此新目录；调用既有 Planner组件，不启动应用服务；
  使用生产 Urllib transport；保存完整 JSON response（解析后序列化，不声称HTTP原始字节），完整 output_text 另存。
- [execution-started.json](execution-started.json)：执行前脚本摘要与独占启动标记，防止误重跑。
- [execution-summary.json](execution-summary.json)：实际次数、逐次状态、耗时与终止时间。
- 每组 `evidence/attempt-NN/`：`provider-request.json`、实际 `provider-request-body.json`、
  `prompt.txt`、原始 `input.txt`、解析后的 `input.json`、`provider-response.json`、
  `provider-output-text.txt`、`provider-draft.json`、`normalized-draft.json`、
  `structure.json`、`attempt-result.json`、request/response metadata；恢复轮另外保存完整 feedback。
- 每组 `state/`：独立 `result.json` 和 `final-plan.json`。没有数据库请求、服务状态或物理执行产物。
- `analyze.py`（仅原始本机目录保存）、[offline-evidence-analysis.json](offline-evidence-analysis.json)：零网络复核请求冻结、
  feedback精确对应、逐字段diff、正常化结果和完整校验结果。

在原始本机目录，离线复核可运行 `PYTHONDONTWRITEBYTECODE=1 /tmp/roboguide-mi-coordination/.venv/bin/python -B analyze.py --output /tmp/ep51-mi-recheck.json`，
指定尚不存在的输出文件；脚本采用独占创建，不覆盖现有分析结果。不要重复执行 `study.py run`；
既有启动标记会拒绝重复调用。任何新增真实模型实验需要新的用户授权和预注册。

原工作树已有 `experiments/` 未跟踪内容保持不变；实验代码视图无 tracked/untracked 改动。
只更新了 Git 远端追踪引用以核对 main；没有 checkout、commit、push、merge 或改动历史目录。
