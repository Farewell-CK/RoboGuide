# Robonix 运动取消与停止：P3 进入前的对接缺口

核对日期：2026-11-05。本文是待补齐的接入要求，不是已经提供的接口或设备停止证明。
职责沿用 [ADR-0231](../decisions/0231-local-eaios-and-device-integration.md)：RG 管理原任务、
占用和恢复，Robonix 决定本机动作、终止及即时安全。RG 不直接调用底盘来补停。

## 当前已核实的边界

本轮 P1 的趴下、站立、重复 Mission 和任务竞争已通过正常 RG 入口验证。
本轮以下核对仅查看真实源码及已发布合同，没有调用导航、ControlPlan、CancelAll
或底盘 stop。新启动的狗2当前原生 Executor 提供 execute、execute_stream、control_plan、
list_active_plans、cancel_all_plans 等合同；builtin 查询不能等同于外部持久任务查询 API。

| 原生位置 | 当前实现事实 | 缺少的保证 |
| --- | --- | --- |
| Pilot `system/pilot/src/service.rs:388` | session-scoped abort 返回 stop requested 或该会话无活动 turn/plan | 不代表原任务的所有设备副作用已终止 |
| Executor `system/executor/src/active_plans.rs:397` | ControlPlan 取消并等待计划从活动表消失；未找到计划时也可返回 true | 活动表消失或未知计划不能单独作为设备停止证明 |
| Executor `system/executor/src/dispatch/async_poll.rs:55` | 发现取消后发送 provider cancel，立即返回 Canceled，不再等待原 run 的最终状态 | provider 仍可能在取消中，计划根终态早于实际本机任务终止 |
| Nav2 wrapper `nav2_wrapper/atlas_bridge.py:1237` | 可按 run_id 查内存状态；cancel 返回 accepted/detail；空 run_id 会解析最近任务 | 原 run 必须精确归属，受理和最终状态分开，重启后未知不能解释成停止 |
| 当前 RG 本机任务 profile | 只声明 no-effects，不声明设备停止能力 | 已发运动计划时继续保留未知和原占用；不能借用通用静止状态补造安全证明 |

本次实际源摘要：

- Executor active_plans：`ce88241ced30359ce17eba5ab7b58a18f7794b069a999edd632ebb56da50c9ef`。
- Executor async_poll：`4f1757f30cd0c4a30bb5237d953efcb060d022cf64f0c7504520174847163919`。
- Pilot service：`1f0ca4e4f89f56c8cc40b29251f3a9fd8d1251f5f5f20b4f59137a092272b052`。
- 导航 wrapper：`a01d22032eede525c354721e634e285deef0b586519b5ce199603cadf61ebb1d`。

这些源码来自 Thor 的实际 checkout，复制及摘要保存在
`~/rg-lab/p1-validation-20261105/p2-readonly-source-audit/`；不能把源核对当作运动停止验收。

## Robonix 侧需要提供的事实

1. **原任务的持久身份与查询。** 保留 session、请求 task、plan、call 与 provider run_id
   的完整关联；查询和取消只针对这一条原链路。不得使用空 run_id、最近任务或 CancelAll
   来代替指定任务。原进程重启后能查回，或明确返回 unknown。
2. **分离受理与终止。** 取消受理后继续查询原 run，只有本机任务真正结束才给终态。
   取消失败、超时、来源替换或查询未知保留不确定，不能把计划移出活动表视为设备停止。
3. **本机权威的停止判据。** Robonix 定义并验证本机停止条件，记录控制归属、反馈来源、
   观测时刻与有效期。任务终态与设备状态须对应同一原任务；RG 不解析 SDK 包或关节阈值。
4. **明确失联策略。** 区分 RG 连接断开、Node 进程结束、Pilot 中断和底盘失联；说明哪些
   情况本机继续、取消或进入安全状态，以及原预算结束后的处置。恢复后不能重发原动作。

具体原生合同变更应在 Robonix 确定并留存版本，不在本文伪装成既有可调用 API。
本机报告再由同一 Connector 适配到已有 Node operation-owned State 和停止合同，
无需为每个基础动作注册一项 RG 全局能力，也不新增逐链路服务进程。

## RG 侧的接收与验收条件

- 核对原 execution generation、Node/LocalSystem/provider 身份、原 session/task/run 关联，
  消费明确的终态及限定有效期的本机停止观察。
- 保留原 stop deadline，不以迟到回执续期；缺证据时保留原占用。管理员取消结案不生成
  StopConfirmed，也不授予自动续跑、交接或替代执行权限。
- 先用隔离的软件 provider 验证取消迟到、受理后仍运行、重启未知及跨任务回执拒绝；
  再在静止狗上验证原任务终止，最后进入现场确认的短路径取消和停止实验。
- 停止及失联策略未补齐前，P3 导航不作为当前已可执行阶段。地图、定位、目标归属和
  当前路线条件也必须分别核实，不能由站立成功推定。

已有自动 no-effects、人工结案和历史保持结果见
[取消现场验收](acceptance/2026-11-05-dog2-cancellation-field-validation.md)及
[本机人工结案验收](acceptance/2026-11-05-local-cancel-settlement.md)。
