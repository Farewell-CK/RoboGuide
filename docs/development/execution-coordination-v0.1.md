# 运行协调与持续 hazard 合同 v0.1

[ADR-0210](../decisions/0210-reviewed-execution-coordination.md)定义本模块。全局 What/Who/Where/When
仍归 Orchestration/Control；Runtime 保留每个原 attempt，Local EAIOS 保留 Immediate How 与最终安全。
没有内置 Node、设备、SDK 命令、抓取动作或部署坐标。

## 原权限与切换

现有 Mission 的独立原目标、原租户、原接受时间和 Context 安全合同不随新计划改变。
先按原 runtime revision 合同安装有界策略并申请 candidate ticket；实际 recovery-admin 使用
`roboguide.runtime-plan-revision/v0.6` 的 `authorize-coordination`，在 output、provider claim
和 Review 之前冻结完整原 Group/membership/attempts、实际 operation recovery/safety 声明、
协调种类和允许的 canonical successor operations。原元数据总量另限 512 KiB，不释放历史换预算。

种类包括 `running-preemption`、`coupled-recovery`、`handoff`、`regrasp`。运行中抢占要求
同租户、严格较高逻辑优先级、当前 Ready 且已到起始时间的受益 Mission；正常 Match 必须证明
它确实可以使用当前源 Group 持有的原 exclusive resource。权限只声明目的，不预先分配资源。
耦合恢复要求多个真实原 attempt 与耦合 Context/Task；旧的独立 Role continuation 门禁保持。

实际 proposer 提交候选后，另一实际管理主体独立 Review。Review 同时绑定 plan、原完成条件、
协调权限 digest 与原 Memory 声明；已经满足的 Task 必须原样保留，不能换 ID 重做相同 operation/参数。
随后原 `request-switch` 在一个 authority transaction 中保留 hold、所有原 stop 和 Memory revoke。
所有原 physical attempts 的独立确认与 provider revoke 齐备前，不创建或执行替代 Group。

新 Group 仍走正常 Control。运行中抢占还必须在同一事务中先让受益 Mission 通过正常
Match/Commit/Bind 获得原冲突资源，并保留其实际 Runtime intent/资源/原 kernel 预算证明。
若整个受益 Task 无法获得资源，切换整体回滚，原已停止 Group 继续持有所有权；不能让恢复的
低优先级任务先抢回资源。原 switch deadline、调用者、命令 ID 和预算都不续期。

设备类协调必须有原始 Memory/context transfer；handoff/regrasp 还明确要求
`DeviceObservation`。软件进程退出不能升级为设备证据。具体安全保持、释放、补偿和恢复语义
由实际 Local EAIOS 的 canonical operations 与其 recovery 声明实现；无法证明时继续持有/人工处理。
模型只能提供语义候选，原 generation/SQL nonce/独立 Review/原目标合同继续适用。

## 活动 hazard

显式 decision-policy registry v0.3 的 Readiness gate 可声明 `active_stop_timeout_ms`。
每个实际 attempt 在投递前冻结原 gate revision/digest；持续检查仍读取原 source、schema、
当前 session、receive-time TTL、置信度和冲突规则。条件失效、来源不可用、规则撤销或扩大保护
都触发原完整 Group stop/hold 与 Memory revoke。规则删除不移除原保护，也不更新原 deadline。

每次 outbox 投递都重查 gate；未配置此功能的 Group 保持原时钟读取次数。原 State 决策回执
进入现有 EventLog，Mission 的 plan-revisions 只读视图含 `active_hazards`，不凭读操作续权限。
Cancel receipt、进程终态、独立原进程 wait 与 DeviceObservation 仍是不同证据。

## 持久化、恢复与验收

Controller wrapper v59 / recovery v0.28 显式保留新权威，包含 mandatory hazard table、
协调原输入/原命令/Review/源版本/完整 attempts 与受益 admission；旧 wrapper 不能隐藏这些字段。
原 v58 topology、v57 scoped Memory、所有 SQL/CAS/Replica/upgrade 合同保留原义。
恢复先独立核对原引用、digest、generation、预算和 stop joins，不启动服务、不补发旧未知结果。
Node v0.14/config v0.15/catalog v0.17/journal v15 与 canonical MissionPlan 合同均不改义。

维护入口：`tools/quality/check_execution_coordination_processes.py --output <fresh-root>`。
本机还通过 `--previous-controller <genuine-v58-binary>` 核对实际旧 writer 拒绝；CI 未提供旧程序
时此项明确为 unavailable，不替代保存的 genuine 证据。每一独立案例保留失败并继续下一案例。
实际 CPU profile 由本机软件 Node 服务与共址 Local EAIOS 组成；原 coordinator 真正创建、等待
并回收全部 CPU children 后才发布 v0.3 完整 Group process-exit。延迟 Cancel 的故障注入不伪造退出。

具体实际结果见[本模块验收](acceptance/2026-10-25-execution-coordination.md)。跨主机、全局内容/HA 与最终固定 profile 继续归 A1/A2/E/F；
设备、物理 regrasp/交接和长期 SLO 证据独立保留，不用软件进程结果替代。
