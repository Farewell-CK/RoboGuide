# ADR-0250：显式本机执行 Unknown 的非终态映射

状态：实现及隔离软件验收通过；未部署现场，真机效果未验收。日期：2026-11-11。

## 问题

Robonix 适配器在任务结果或物理停止尚未证明时会持久化并返回 `UNKNOWN`。
Node 的声明式执行映射只支持 Accepted/Running/Completed/Failed/Cancelled，配置片段也
未声明 UNKNOWN。有效的本机未知报告因此变成 `unmapped state`，经过五次观测重取后才
间接发布 Unknown，且通用错误覆盖了原始诊断。本轮失败原证据保留在
[双狗导航验收记录](../development/acceptance/2026-11-11-two-dog-navigation-live.md)。

## 决定

`core/node-service` 的 `ExecutionStateMappingConfig` 增加可选 `unknown` 字符串列表，
缺省为空；沿现有大小写规范化、重复值检查及工作流摘要冻结机制编译为明确的
`MappedExecutionPhase::Unknown`。未声明的状态继续拒绝，不能从字符串猜测权限。
这是现有配置中的可选映射扩展，不改变 Node Protocol、Node Contract 或 journal 代际；
旧配置含义不变，旧程序的 `deny_unknown_fields` 拒绝含新字段的配置，不能静默忽略。
映射改变后的活动工作流仍受原摘要漂移检查约束，不能直接冒用旧 attempt 续跑。

有效 Unknown 立即保存 `JournalStatus::ReconciliationRequired` 并发布既有 Wire Unknown，
保留原始 detail 与原 attempt/handle/资源锁。它不属于观测失败预算，不映射为 Running、
Failed、Cancelled 或 Completed。相同 detail 的重复报告不增长执行序号；新的 Unknown
detail 替换旧诊断时保留原事件序列。轮询只继续读取同一持久 handle，绝不重新 Execute。
本机原 handle 后续确切终态仍可沿既有事实通路报告；这不授予 Controller 自动解除
安全停止、释放占用、接替或重试权限。进程恢复继续使用原 journal 与同 handle 查询。

`integrations/robonix-task/deploy/node.task.fragment.toml` 显式声明 `unknown = ["UNKNOWN"]`。
适配器仍拥有原生执行结果判定；Node 只保真映射，不新增 Robonix 调用或独立服务。
Mission 当前 fail-fast、全组停止及物理停止证明合同保持不变；`independent` 协作模式
不因此获得任务失败隔离语义。

## 验收边界

软件回归覆盖默认空列表、精确原 detail、大小写与冲突拒绝、真实部署片段、首次有效
Unknown、重复报告幂等、锁和 attempt 保留、重复 Execute 不重发、原 handle 后续终态，
以及本地 runtime 结束后重新打开 SQL/recover 只读轮询。测试使用内存驱动，不访问现场。
旧传输失败重取与取消测试继续保留。代码、隔离软件、现场部署和物理到达/停止分别记账；
修复不能消除原生模型的 `truncated JSON response`，也不能把 Cancel 回执当作停止证明。

ThinkPad冻结source-r2后，原3项夹具失败定向复验及全部150项Node单测通过，Node全部
target/feature的Clippy通过，Node格式检查通过。原全仓格式失败（两个未改动State文件）、
首轮3项测试夹具失败及脚本误用旧缓存的失败全部保留；没有调整生产停止/锁合同。
具体来源、缓存修复、测试程序摘要和现场未结案状态见上述验收记录。
