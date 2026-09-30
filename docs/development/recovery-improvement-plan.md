# RoboGuide 恢复能力完善计划

> Proposed for review。本文是工程计划，不代表已实现自动停滞恢复或执行期重新规划。
> 调查基线为 `dev@4dd8063219c3fb09def3aa698b2fceb6075454e1`。

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
| 本地执行卡住、明确失败 | 现有本地技能状态、终态与诊断证据 | 通用进度判定和分类恢复策略尚需设计 |

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

通用验收必须覆盖重复故障去重、取消/完成竞态、旧 attempt 的迟到消息、Controller/Node
重启、资源释放失败、局部与全 Group 释放区别、多个 Mission 隔离和恢复预算耗尽。
不能确认物理动作停止就是恢复 blocker。最终仍分别报告 Mission satisfaction 与官方
benchmark outcome；没有获得官方真值时保持 unavailable。

恢复预算记录所有 Planner/Repairer 调用、本地重试和物理 attempts，分别有次数与总时间
上限。预算耗尽保存真实失败，不能生成新 Request 规避限制。配对公平性和 Formal population
继续按现有规则独立判定，不能因为恢复未成功而删除样本。
