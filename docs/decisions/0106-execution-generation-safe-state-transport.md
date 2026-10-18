# ADR-0106：Attempt Generation 到 Local EAIOS 的安全状态传递

状态：Implemented on `codex/e1-failure-closure`，待主干审查。日期：2026-10-18。

## 背景

SafetyStop 的确认需要当前物理 attempt 的 `execution_id`、`execution_generation` 和
Cancel command identity。原来的 Node Execute 只把 attempt ID 传到 Local EAIOS，适配器
无法安全地从本地字符串猜测 generation。缺少 generation 时，Controller 必须保持
`ManualEscalation`，不能释放 ownership 或把 Mission 标记为已安全停止。

## 决策

Node Protocol v0.4 的当前 additive Execute wire 增加 `execution_generation` 字段。声明
`roboguide.node.v0.8` 的 Node 必须收到非零 generation；缺失值在 Node 侧产生明确的
拒绝回执，不启动 Local How。旧 Node Contract 保持兼容，仍使用 generation 为零的旧路径，
但不能声明或消费 SafetyStop safe-state profile。

Node Service 将该字段写入本地 canonical invocation，Local EAIOS 只把它作为 attempt-bound
证据身份使用。它不改变 `ExecutionIntent`、目标、资源、动作选择或 Habitat 官方判定。

Habitat Local EAIOS 通过只读 `GET /v1/executions/safe-state` State Export 提供
`roboguide.safe-state-observation/v0.2`。只有同时满足以下条件的本地记录才会出现：

- execution 已进入真实 terminal state；
- Controller 的取消请求已被本地记录；
- 本地 outcome 已保存；
- invocation 含非零 execution generation。

其他情况返回空 observation，Controller 将其视为 unavailable。Cancel receipt、空 progress、
进程重启失败记录和自然完成都不能单独生成 safe-state 证据。该软件证据只表示 Local
EAIOS 的 execution worker 已停止，不建立真实设备停止或硬实时安全保证。

## 验收边界

确定性测试覆盖 generation 缺失拒绝、generation 保真传输、terminal/cancel/outcome 组合、
大批量 observation 不截断以及错误 command/generation/assurance 拒绝。真实设备安全、
传感器真实性和 hazard model 仍需独立部署验收。
