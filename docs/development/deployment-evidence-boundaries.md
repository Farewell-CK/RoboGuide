# 部署证据解析与消费者边界

O7 在既有 application 模块中分离数据解析与执行消费者；不引入新 crate、sidecar 或
权威。已接纳的来源仍保持 exact digest、snapshot identity、lifetime、预算和未知状态。
Core 合同、MI episode/dataset provenance、Control Commit/Bind 和官方结果不变。

| 责任 | 当前实现 | 能力边界 |
| --- | --- | --- |
| 具体 reset / endpoint / operation source 解码 | `deployment_sources.rs` | 严格 schema、完整 source 覆盖、有限记录数、事实/结论一致性；无 Controller、Control 或 Runtime 写 API |
| 跨语言 source identity | `deployment_document.rs` | 原 float-bit canonical digest、SHA 语法；不能决定资格或 placement |
| 已校验的候选证据消费者 | `deployment_feasibility.rs`、`deployment_operation_admission.rs` | exact-intent 约束、配置拓扑、source-bound operation 参数；只限制当前 eligible candidates，不能选定或绑定执行者 |
| 初始成本与支持反馈消费者 | `initial_operation_preferences.rs`、`initial_operation_assessment.rs` | unknown 不排除 Node，预算和 source lifetime 不续期；消费同一来源 identity |
| 中性 verifier source / verdict codec | `verifier_source.rs` | 有界文件、schema/digest/predicate/attempt 形状与来源一致性；不认识 Habitat 官方指标，不改变 lifecycle |
| 中性 plan / current-attempt / satisfaction 消费者 | `task_verifier.rs` | 接受计划和原有 freshness/attempt/source 校验；在原 application 事务中调用 Orchestration，仍尊重 Mission isolation |
| 实际官方证据 producer | `integrations/habitat-local-eaios/` | 读取原环境结果并发布 neutral evidence；无 Commit、Binding 或 TaskSatisfied 写权 |

`FrozenFeasibilitySnapshot` 保留实际 run/episode/scene/dataset、reset positions 和
Node-to-agent provenance；这些是来源交叉核验输入，不能删除，也不把它们解释成动态
executor inventory。消费者不解析 floor、seed、agent/profile 的 vendor 规则。
`TaskVerifierFeed` 的来源字段保持私有，消费者通过固定 contract/predicate 检查及
只读 getter 使用证据，不能改动已经校验的来源身份。

既有 v0.3/v0.4/v0.5 deployment snapshots、operation-admission v0.1 和 verifier v0.1
保留原 wire shape、默认/禁用路径和错误规则。来源与 typed failure 穿过内部模块接口，
不以文件缩短或 LOC 作为完成证据。137 个 application 测试覆盖实际 HTTP admission、
默认路径、unknown、反例、tamper、来源变更、joint coverage、checkpoint restore 和
Python producer fixture；移动后的 codec 专项测试留在对应模块。
