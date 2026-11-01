# ADR-0231：显式 Provider 草案与宿主保管的 Mission 目标

- 状态：已实现，软件验证见分支交付记录；真实模型及物理接入由新批次独立验证。
- 日期：2026-11-01
- 范围：Mission Intelligence 的模型草案边界。

## 问题

E2 1031 试跑把任务、动作规则、预算、观测图和历史反馈拼进 `GroundedIntent.objective`。
苹果第一段 8,825 字符仅两处换行变空格，烤串第二段 12,205 字符缺少 46 字符动作说明，
咖啡杯第三段 19,537 字符增加 210 字符节点文本，均被原目标逐字校验拒绝。
原始批次和失败保留；后两例存在内容差异，不能统一视为排版问题。

V2/ADR-0037 已将目标与带来源的上下文分开。通用 MI 仍要求模型生成宿主已持有的目标字段，
使无关复述错误成为不可恢复的身份错误。文字一致也不能证明生成动作满足原目标。

## 决定

1. `mission.provider_plan_profile` 在启动配置中显式选择 `host-objective-v1`。
   缺省和显式 `echo-objective-v1` 保持原来的逐字目标校验；未知值启动拒绝。
   该选择进入服务配置 identity 和实际模型请求，不能对原 deliberation 静默换档。
   `mission.prompts.host_objective_path` 可显式覆盖版本化附加 Prompt；默认资产是
   `mission/prompts/v0/host-objective.md`。新格式在启动时冻结其摘要，漂移在模型调用前拒绝。
2. 新 Provider 草案保留选定的 canonical `schema_version`，增加必需的
   `provider_profile: host-objective-v1` 标记，且禁止 `mission.objective`。
   `mission.id` 仍按本次请求的精确 ID 约束。v0.8/v0.9/v0.10/v0.11 分别显式选择，
   新格式不借用旧 v0.7 输出兼容。旧格式拒绝新标记，缺标记或不同 profile 不作推断。
3. MI 从本次实际冻结的 `GroundedIntent.objective` 确定性组装完整 MissionPlan。
   不去空白、不摘要、不做模糊等价判断，也不从另一 Request、数据库当前视图或模型输出
   补目标。即使模型输出完全相同的 objective，新格式仍拒绝，保留原始拒绝草案。
   这是调用前声明的 DTO 转换，不是收到错误目标后的覆盖修复。
4. Planner、草案 regenerate、Repairer、Repairer regenerate、运行期 replan 和部署
   reconsideration 的嵌套 replacement_plan 都走相同边界。Reviewer 获得完整组装计划、
   原 GroundedIntent 与同一 grounding，不生成或修改目标。
5. 完整 canonical 解析、身份、Catalog、物理身份来源、满足条件、配置的精确原目标门禁、
   Controller 只读前检、独立 Review、审批和正常提交继续执行。宿主组装目标不证明动作
   正确，也不建立自然语言目标的一般确定性语义判定。
6. 服务实际 Provider-call provenance 可附带 `roboguide.provider-plan-assembly/v0.1`
   观察：原完整 GroundedIntent 摘要、模型原始草案摘要、组装计划摘要及有界原始草案。
   超出原 per-call byte budget 的原始内容明确标为 truncated 并保留完整摘要；摘要不
   冒充已留存内容。拒绝草案沿用原 raw/normalized 证据及预算；实际运行期 transport 和
   E2 recorder 继续各自保留原响应。组装观察不提供提交或目标满足权限。
   原调用准入仍按原 metadata 的计数/字节预算判断；可选组装观察只使用剩余空间。
   空间紧张时，后续快照可省略较早的可选观察，原调用元数据及已持久化历史不改写。

## 兼容性与验证

Canonical MissionPlan、Controller、Node 和物理执行格式沿用选定版本。Profile 是新增显式
模型输出格式，启动配置不得交给旧实现后假定启用；接入方必须检查实际请求 schema/profile、
实际导入的 MI 路径和原目标完整性。旧实现的 canonical 接收端拒绝新草案额外标记及缺少目标，
不能直接把新草案交给 Controller。

离线验证覆盖所有生成入口、三种历史长度、显式各代 schema、跨 profile 拒绝、禁止目标回显、
错误 Mission ID、未知 Operation、对象/关系/条件被改动时的精确原目标拒绝，以及原始证据边界。
真实模型输出质量、token/耗时和物理任务结果须在保留原批次的新目录中独立观测。

接入说明：[MI 宿主目标验证指南](../development/mi-host-objective-v0.1.md)。

## 提交后复核修正

对首版 `200e7a4e` 的复核发现并修复三个问题：

- 7,000 字符原始草案内联到 trace 会把原 64 次调用上限提前压到 15 次。可选观察现在
  在准入前让出空间；相同原 metadata 的 ASCII/Unicode 调用序列保留原计数与字节拒绝点。
- runtime worker 原 prompt identity 没有纳入新增的 host-objective 资产。现在该文件参与
  原身份摘要；仅改变它后重启，真实 composition/SQLite 的原 work 即因 drift 拒绝，
  原 work、预算和 effect 表保留。
- 附加 Prompt 中不限定范围的 objective 禁止语句可能误导模型省略 Role 的必要字段。
  现在明确只省略 mission.objective，保留 Role execution_intent.objective。

专项回归通过不代表真实模型会稳定遵循 Prompt，物理批次仍须独立验证。
