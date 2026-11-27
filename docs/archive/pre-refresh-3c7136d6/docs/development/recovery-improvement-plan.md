# RoboGuide 恢复能力完善计划

> typed MI 恢复、完整 HTTP body 绑定的接纳对账、只读操作进度，以及明确授权的停止后
> Role 恢复已实现，见 ADR-0054..0057。Habitat 可选导航进度 observer 见 ADR-0058。
> ADR-0059 已补齐 deployment-owned 停止范围与原上下文重执行声明、冻结与逐阶段检查。
> 自动停滞策略、shared-world 独立 Role 停止/重试与执行期新版本 MI 计划仍待验证/设计。
> 调查基线为 `dev@4dd8063219c3fb09def3aa698b2fceb6075454e1`。

> 2026-10-04 更新：ADR-0067 已补齐默认关闭的提交前 MI 部署重检。新 assessment v0.2
> 给出当次 Control 排除原因计数；Responses Repairer 在持久化预算内提议重查、修订
> 或等待。修订重过 Reviewer、审批及 Control，不读取实时 Node inventory，不改变
> 已提交计划。详见第 6 节；真实模型表现和物理效果仍须另外验证。

## 1. 先区分失败位置与执行权威

“机器收到前出错”不是充分的安全重试条件。必须区分尚未提交的草案、已被 Control
接受或 Commit 的计划、已发出但回执缺失的 Execute，以及已经启动的物理 execution。

| 情况 | 当前能力 | 后续处理 |
| --- | --- | --- |
| 草案结构无效 | Planner prevalidation regeneration，保存每份拒绝输出，次数有界 | 保持原任务与同一 grounding snapshot，重新完整校验 |
| Reviewer 发现可修复语义问题 | `RepairPlan` 路由至 Repairer，再次校验和审查 | 不能删除目标、虚构部署事实或绕过审批 |
| 用户任务缺少必要语义信息 | `NeedsClarification` | 向用户澄清，不能用模型补造事实 |
| Provider 认证或传输失败 | 不进入草案恢复 | 按基础设施错误处理；只有有依据的传输重试可重试 |
| 暂时无资源或候选节点 | Control durable scheduling deferral | 等待新的真实可用性，不能删约束以通过 |
| Execute 已发出，是否启动不明 | Runtime `Unknown`、durable command identity、Control reconciliation | 先对账，不能把超时当作“机器没收到” |
| 本地执行卡住、明确失败 | 现有本地技能状态、终态与诊断证据 | ADR-0056 提供只读量度；显式停止后 Role 恢复见 ADR-0057，自动策略尚未实现 |

证据入口：[`request_engine.py`](../../mission/src/mission/request_engine.py)
`_plan_with_recovery`、`_review_and_advance`；
[`application/recovery.rs`](../../apps/integration-server/src/application/recovery.rs)
`apply_recovery_required`、`resume_role_recovery`；
[`lifecycle.rs`](../../core/orchestration/src/mission/lifecycle.rs) `task_failed`。

现有 Control 恢复主要重用被接纳的 Task requirement 和 canonical operation，换节点后
重新 Match -> Schedule -> Propose -> Commit -> Rebind。它不是通用的“失败后让 LLM 改计划”。
当前最终 Task failure 会结束 Mission；不能宣称这类失败已自动恢复。
[`ADR-0028`](../decisions/0028-durable-command-recovery-and-attempts.md) 也明确不提供
exactly-once physical action 或通用 recovery optimizer。

## 2. 第一阶段：下发前形成明确的恢复分类

当前 `mission.recovery` 和 Request Engine 已实现该分类切片。恢复 evidence 在独立
observations v0.2 中随 Request 原子保存；公共 Request v0.4、MissionPlan 与 Core contracts
不变。提交结果不明时只查询原 Mission，不能用恢复诊断补造完整接纳 receipt。
独立 observations v0.3 和完整 HTTP body 绑定的权威接纳查询已补齐，见
[ADR-0055](../decisions/0055-controller-admission-reconciliation.md)。原始 POST evidence
保持不变；只有完整匹配的权威 receipt 才能恢复 Accepted。旧 identity 查询仍不证明接纳。

先补充跨 MI、Controller admission 与 dispatch 边界的失败矩阵及可复核测试，复用现有预算，
不统一把错误发送给模型。需要统一记录：失败 stage、稳定 reason、request/draft/context digest、
是否已接受或 Commit、是否存在 durable command/Node receipt，以及下一次允许动作。

模型输出错误继续在同一 Request 的草案闭环内修复。资源缺失继续由 Control 等待。
Provider 故障和部署配置错误进入明确的运维状态。已接受计划的改变不能直接复用草案 Repairer
覆盖原计划；若确需替换，要先设计版本、撤销与重新接纳边界。

验收：拒绝草案不 dispatch；修复保留所有原目标；相同 request_id 恢复观察不重复创建请求；
HTTP 超时或缺失 receipt 不产生新的物理 command identity；无可行节点保留 pending。

## 3. 第二阶段：先可靠判断“没有进展”，再决定如何恢复

Node/Local EAIOS 报告绑定当前 execution attempt 的进度证据：当前 operation、观测时间与
freshness、operation-specific progress、是否可安全取消，以及直接 failure reason。
心跳只证明活着，不能证明任务推进；等待、避障与持续维护任务也不能统一按“没有位移”判失败。
操作进度要由本地能力契约提供，不能在 Core 硬编码 Habitat 距离或导航步数。

先做只观察的停滞判定，验证误报和重启语义，再开启策略。诊断数据不足时记录 Unknown，
不擅自升级成失败。失败分类至少区分 transient local error、tool contract violation、
skill budget exhausted、capability/feasibility failure、terminal-world budget 与 physical ambiguity。
当前已有诊断字段不得冒充已存在的通用 Node progress contract；新增公共证据需独立 ADR
和明确版本，不把 adapter JSON 或日志字符串直接当 Core 的重试协议。

验收：正常等待不误报；长时间无进展有确切源证据；过期、跨 attempt、旧 owner、乱序或
重启后证据不能触发新执行；采集失败不会改变健康、生命周期或技能动作。

## 4. 第三阶段：按层级恢复，保留原语义目标

1. **本地恢复**：本地系统在相同 canonical operation、参数和预算内处理路径重算、
   避障和临时错误。错误工具调用可以按已声明策略反馈给本地模型，但 Guard 不替换目标。
   EMOS 原始策略与新增本地恢复策略必须作为实验配置差异记录。
2. **执行恢复**：本地已不能继续时，Runtime 保存当前 attempt 与原始错误，Control 决定
   是否重试或重新匹配。新执行必须有独立 attempt identity；只有可信的停止证据或能保证
   旧 owner 无法继续动作的 fencing 后，才允许冲突的替代执行。Cancel receipt 不是已停止。
   这里的 fencing 必须阻止真实本地动作；忽略旧 attempt 的迟到消息不等于停止机器人。
   Actor 的物理绑定、资源和未受影响任务必须遵守原契约。
3. **计划恢复**：真实需求或已证实的能力缺口需要改变任务组织时，提供归因明确的新世界
   证据供 MI 提议替代计划，重新完整审查和 Control 接纳。模型不直接选择 Node，不写入
   resource reservation，不修改官方 benchmark 目标。

不能保证停止时保持 Blocked/Unknown 并请求人工介入。无替代节点时保持 pending。
原 Task 已完成的物理效果和联合终态要求不能因重规划被丢弃；新计划必须保留这些证据与约束。
针对非幂等动作，还必须判断失败前是否已产生部分副作用，不能仅凭 error 再执行一次。

该阶段涉及运行期策略与可能的公共证据契约，须先更新 V2/ADR，默认关闭，先 fake-node
验证，再受控真实诊断。不能顺带改变当前配对实验的固定执行策略。
新的 deliberation 必须有明确 identity 和新 snapshot digest，保留旧计划、review 与证据。
当前 B1 要求同一 Request 的 review history 绑定同一冻结 grounding digest，不能通过
覆盖 request record 的 context 或重写历史 review 来偷渡执行期重新规划。

## 5. 全链验收和实施顺序

先补齐配对观测及 Provider 返回身份，冻结两臂配置，避免优化前后混用结果。随后按
下发前分类 -> 只读进度观测 -> 有界本地恢复 -> 可靠停止后的 Control 恢复 -> MI 计划恢复推进。
每阶段先 deterministic tests，再独立受控诊断，不按成功结果追加尝试。

## 6. 已实现的提交前部署重检边界

[ADR-0067](../decisions/0067-bounded-mi-deployment-reconsideration.md) 复用现有 Repairer
增加 `deployment_recovery` 模式；普通语义 RepairPlan 路由不变。输入是当前已审查草案、
同一冻结任务/grounding/catalog/policy/profile 和来源绑定的 assessment v0.2。
输出只有 `recheck`、`revise_plan` 或 `wait_for_evidence`。候选排除计数来自当前 Matching
的同一谓词，而不是从之后健康快照反推原因；反馈不携带具体 Node/Resource inventory。

模型调用前保存 pending attempt、完整 input、digest 和原次数/时限。默认 0 次，显式
启用最多 3 次，最长 900 秒；调用等待受剩余时限限制，迟到结果不应用，重启关闭 pending
但不恢复预算。相同反馈、来源或 context 变更、过期及提交不明都阻止再次模型恢复。
明确的新 dialogue 不重置 Request 预算，也不能把旧 context 当成新模型输入的证据。

完整修订必须再经过既有确定性 admission、独立 Reviewer、必要的新 revision 风险批准
和 Control 当前预检。没有自动合并 Actor、删除 Task、降低协作、创造能力或目标修正。
无法给出有原任务/部署依据的修订时保留 hold。保存全量、有界原始模型输出和 canonical
decision；新版 observations v0.4 的 session 由 restore 和 B1 provenance 检查。
现有 Formal、semantic omission diagnostic 与官方 benchmark 规则保持独立。

实现入口：`request_engine.py::_recover_deployment_hold`、
`responses.py::ResponsesMissionRepairer.reconsider_deployment`、
`core/control/src/matching.rs::first_use_candidate_diagnostics`；
启动选项为 `ROBOGUIDE_B1_DEPLOYMENT_RECOVERY_ATTEMPTS=0..3`。架构路径图已同步到
[V2 当前部署视图](../architecture/v2/README.md)。离线回归覆盖 authority fence 和真实
Responses 适配/规范化，并不证明模型会提出有效的任务组织，亦不证明 Episode3 已解决。

通用验收必须覆盖重复故障去重、取消/完成竞态、旧 attempt 的迟到消息、Controller/Node
重启、资源释放失败、局部与全 Group 释放区别、多个 Mission 隔离和恢复预算耗尽。
不能确认物理动作停止就是恢复 blocker。最终仍分别报告 Mission satisfaction 与官方
benchmark outcome；没有获得官方真值时保持 unavailable。

恢复预算记录所有 Planner/Repairer 调用、本地重试和物理 attempts，分别有次数与总时间
上限。预算耗尽保存真实失败，不能生成新 Request 规避限制。配对公平性和 Formal population
继续按现有规则独立判定，不能因为恢复未成功而删除样本。

## 当前已验证的通用切片（2026-10-01）

1. 同 Dialogue 重试复用冻结 grounding；新 Dialogue 完整旧周期进入 SQLite immutable
   history。认证/配置由显式 retry 恢复，408/429/5xx 留在基础设施分支。
2. 发送前保存实际 HTTP bytes 指纹。`GET /v1/missions/{id}/admission` 使用 Controller
   原事务内保存的 SHA256/identity；只在与原稿和原发送一致时恢复 Accepted。失败 POST
   原证据保留，legacy/缺失/不符 proof 不解开提交 fence。
3. Node 配置 operation-owned State progress export 后，Runtime 使用原 receive time/TTL
   记录单条 counter/activity 与 attribution；心跳不是进展，Waiting 不是 Stalled。未配置
   observer、信息不足或过期为 Unknown。观测不自动 Cancel 或改写任务。
4. 显式 `/recover` 授权在原 attempt/owner 上发 durable Cancel，真实 Cancelled 才允许
   Control partial release。原 owner 可经单独 stopped-owner Matching 再次成为候选，
   保持 Actor 身份、eligibility 和所有 Commit/Rebind 检查；重试仍有新的 attempt ID。
   未停止保留旧资源，候选不足保留 pending，预算过期 Abort 未 Rebind 的 replacement
   Commit。普通 Cancel、实际 Failed 与 Completed 各保留原路径。
5. 独立 Role 恢复要求原 operation owner 的 dispatch-frozen `execution` /
   `repeat-after-stop` 声明与当前注册一致。replacement 在 Matching、Commit、stateful
   Rebind 和 Execute delivery 再检查；legacy 缺失不升级，变化不继续。Habitat shared-world
   当前声明 `execution-group` / `unsupported`，在 Cancel 前拒绝 Role 恢复。

完成 deterministic fake-node/实际 HTTP handler 检查不证明所有 vendor 正确提供进度或
真正停止。下一阶段先在明确操作契约下验证一个真实 progress observer 和 Cancelled
履约，再决定自动触发策略。执行期 MI 新计划不能覆盖原 accepted plan 或历史 snapshot。
shared-world 若要继续运行，必须先实现并验证真实的 local stop 与 retained-session
continuation 协议；本轮声明不等于该能力已实现，也不允许以新 reset 取代原世界。
