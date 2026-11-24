# ADR-0232 原任务取消与资源结案

状态：实现候选，尚未部署或进行硬件验证。

## 原因

狗2的原生规划期取消已经回传 Cancelled，但统一 task.delegate 缺少安全声明，
Controller 将原停止目的保留为 ManualEscalation，没有正式的管理员结案入口。
未发出任何执行计划的任务也需要表达有来源的未派发依据。

## 决策

复用 Node 的 operation-owned State export、接收时间、原 generation、原 session、
原取消回执和 Runtime 终态。显式 execution-safety/v0.2 与 safe-state-observation/v0.4
增加 no-effects assurance。它只允许以原任务未派发的证据结束取消占用，不产生
physical StopConfirmed，也不能用于恢复、续跑或设备交接。v0.1 与 v0.2/v0.3 的设备
和原进程停止规则保持原义。状态报告由本机系统产生，Control 不解析目标文本判断无副作用。

管理员 POST /v1/executions/{id}/safety-stop/reconcile 提交 stop-reconciliation/v0.1。
请求固定原停止记录摘要、幂等命令、原 generation、核验内容及 no-effects 或 stopped
结论。仅当原记录已经转人工、原 attempt 仍为当前同代、原任务已进入终态（Cancelled、Failed 或 Completed）、Mission 正在
取消时接纳。身份来自已有认证 principal，不能来自请求体。该操作保留原记录与原期限，
记录新的管理员证据，并复用正常取消收尾。它不写入物理停止证明或授权恢复。

结案证据以显式 stop-settlement/v0.1 存入原 purpose 的可选闭合字段。字段只在新功能
生效时写入。旧 purpose 解码器 deny_unknown_fields，旧 execution-safety 解码器拒绝
v0.2，旧 Node 也拒绝 v0.4 export，因此旧实现不能读取新结案后继续写入。
外层 Controller v60 与恢复 profile 保持原版本，新增权威由这些闭合、显式版本的内部
合同隔离。恢复必须完整复核原记录摘要、归属、接收时刻和结案证据，删除或修改证据失败关闭。

Robonix 入口继续使用同一 Connector。只对持久记录可证实的派发前取消，或原 turn
明确 interrupted 且完整已保存流无计划的场景报告 no-effects。任何计划出现、证据缺失、
来源变化或重启未知都不报告。通用运动取消仍要求本机最终终止及真实设备状态，不能用
Executor root Canceled 或网络 ACK 代替。现有 native 查询不足的部分保留 unavailable。

## 验证

隔离测试覆盖真实 Node State 归属、取消及资源释放、原记录保留、原窗口、迟到、缺字段、
跨任务和代次、重复和冲突请求、管理员权限与租户、冷恢复及证据篡改、旧闭合解码器拒绝。
不以软件测试宣称设备停止。现场服务、旧任务结案和运动验证需另行执行与记录。

## 2026-11-17 维护：失败终态的人工取消结案

Mission 进入 Cancelling 后，当前 attempt 仍可能收到原始 Failed，例如 Node 在派发前
拒绝任务后报告失败。若原停止目的为 ManualEscalation，只接受 Cancelled 会令管理员
无法结案，原资源持续保留。人工结案与冷恢复验证因此同时接纳当前同代的 Cancelled
或 Failed；保留原 Runtime 终态、失败事件、停止记录及期限，不伪造 Cancelled。

Failed 本身不允许释放。仍须原 Mission 正在取消、精确记录摘要、generation、已认证
管理员和显式核验内容，再由正常 apply_pending_cancellations 完成 Mission 取消和资源
释放。人工结案仅用于取消收尾，不授予 resume、handoff 或物理停止确认。
AutomaticNoEffects 继续仅接纳 Cancelled，不能用无副作用观察自动结案 Failed。

回归覆盖两种终态的资源保留、管理员与租户权限、SQL 回滚、命令重放及冲突、取消中与
取消完成后的冷恢复，以及原 Failed 和停止记录保留。现场部署及原任务实际结案另行验收。

## 2026-11-24 维护：并发完成终态的人工取消结案

多机同组（ADR-0134 / ADR-0137）发生全组安全停止并进入 Cancelling 时，同组中另一角色
可能并发上报原始 Completed（例如同组其他任务触发 SafetyStop 瞬间，该节点已完成本地停稳
并回报完成）。若该任务的原停止目的保留为 ManualEscalation，人工结案拒绝 Completed 会导
致整组取消永久死锁、资源槽位无法释放。人工结案与冷恢复验证因此同时接纳当前同代的
Cancelled、Failed 或 Completed；保留原 Runtime 终态、停止记录及期限，不伪造 Cancelled。

Completed 本身同样不允许释放或续跑：仍须原 Mission 正在取消、精确记录摘要、generation、
已认证管理员和显式核验内容，再由 apply_pending_cancellations 完成取消收尾。
AutomaticNoEffects 继续严格仅接纳 Cancelled，不得用无副作用观察自动结案 Failed 或 Completed。
