# 有界主动观测与证据封口

本扩展由 deployment-owned Habitat Local EAIOS 实现。公共 MissionPlan v0.8、Core 的
Proposal/Commit/Bind 与 Task satisfaction/release 权限保持原有边界。实现决策见
[ADR-0094](../decisions/0094-bounded-observation-and-local-contract-recovery.md)。

| 入口 | 可以执行 | 完成证据 |
|---|---|---|
| `observation.verify@v1` | 读取既有缓存，无模型/运动/额外 step | 正或负的有效读数；不证明 condition true |
| `observation.acquire@v1` | 本地有界接近指定实体与观察 | 当前原始缓存明确检测到该实体 |
| Contract recovery | 执行前要求模型重新选择 | 新返回的 action 独立通过相同 Guard |

acquire 的唯一参数是 `expected=detected(entity-ref)`。当前 profile 没有独立相机云台、
全场搜索或任意动作控制能力；预算也不证明超时目标不可能达到。新的 capability attributes
只有来源明确的 acquisition mode、visual modality、允许移动与本地 cycle 上限。
运动方式复用 Node 已有的 `base-type`（如 legged/flying），完整投影到 MI capability class；
主动观测的 `viewpoint-positioning` 则从同一注册事实约束为 ground/flight。Control 使用已有
typed capability constraints 做 Matching；逻辑 Actor 名称和文字描述均不选择物理端点。
当前部署仍未提供 FOV、gimbal 或 visibility guarantee，MI 不得猜测。

当前检测能力通过注册的 `condition-format=detected(entity-ref)` 声明精确参数格式，
该字段完整进入 MI capability class。acquire 的完成条件仅为检测成立；接近实体是
本地采集手段，并不证明已到达目的地。需要同时到达和检测的 Mission 仍须保留独立的
移动意图和 satisfaction 要求，不能将任意联合目标填入这个部署的 `expected` 参数。

deployment v0.4 增加 `enable_observation_acquisition`、
`observation_acquisition_max_steps`（1–4096）与 `contract_recovery_attempts`（0–3）。
它们由声明冻结，子进程实际使用值须与 Local How v0.10 一致；acquire 还要求原始传感器、
语义相机和导航技能可用。默认 Python adapter 开关关闭。新的感知/重排 deployment
模板显式选择本轮开发配置，旧冻结运行继续使用自己的旧模板。

planning world v0.4 的 `entity_geometry` 是最多 64 条 reset position，字段为
`entity_id`、`frame_id`、三项 integer `position_mm` 和 `up_axis=y`；未知位置进入 `gaps`。
它只是一个已观测快照，不能当作 route、future goal 或检测真值。

live `GET /v1/executions/closure` 返回 Mission、session digest、producer phase 和实际
publication flags。`closed` 表示发布尝试已经结束，只有 `evidence_flushed=true` 且无归档
错误时才能声称必要终态证据已封口。实际执行失败仍可拥有完整的失败归档。
Runner 最多观察 30 秒 / 64 页小状态响应，将每次读取保存在
`evidence-closure-archive.json`；失败归属 `HARNESS_ARCHIVAL`，不覆盖 `run-failure.json`。
legacy adapter 的 closure 为 unavailable；只有 live 的已接受 Request 启用该附加 barrier。

feasibility v0.6 的 `finality_profile` 固定三个中立字段：
`verifier_publication=after-all-session-slots`、`endpoint_capacity=1`、
`resource_release=after-task-satisfaction`。Controller 在资源保留的 final slots 上进行
有界候选匹配，并在创建 Group 前拒绝无法闭合的容量冲突；这不授予任何预留或释放权限。

感知与重排的 live deployment 还在每次 reset 后、Mission Service 启动前发布
`roboguide.execution-planning-evidence/v0.1`。该 artifact 将同一份 semantic evidence 的
`run/episode/scene/dataset` identity、Habitat 官方 terminal verifier source，以及
endpoint 数量、capacity-one、resource release 和 verifier publication 绑定到一个 digest。
Planner、Reviewer 和 Repairer 看到的是这份不可变的 deployment declaration；它不是 live
Node inventory，也不选择 Actor 或 Physical Entity。MI 检查同一 Task 的同时 Role 需求和
终态 verifier 在所有 session slots 结束前持有的容量；independent Tasks 只有在 Control
实际释放后才能复用 endpoint。Planner/regenerate/Repairer 的新草案还通过 Controller
`/v1/missions/validate-plan` 复用提交时的发布合同校验，保留原始草案与多条结构化错误，
进入原有有界恢复。HTTP 失败、缺失接口和来源不匹配保持系统失败；前检不能授予提交、
调度、资源或物理可达性保证。需同步部署包含该接口的 Controller，不能回退为跳过前检。
逐 Task 可及时发布的独立 verifier 不受全局终态 source 的生命周期限制。
详见 [ADR-0109](../decisions/0109-published-plan-validation-and-bounded-draft-recovery.md)。缺失、篡改或跨 reset
的 artifact 会让 live Mission Service 在请求前 fail closed；offline prepare 可以只验证配置
模板，不宣称已经拥有本次 reset 的 verifier evidence。

真实验证必须同时保留 frozen input、精确源码/model/部署 identity、MI 请求与草案、
Control assignments、实际动作和反馈、initial/reset/terminal diagnostics、官方结果和
closure/provenance/admission。应分别汇报 true、false、unavailable，不能将本地 acquisition
完成当作联合官方成功，也不能把小规模诊断当成 E1 成绩。
