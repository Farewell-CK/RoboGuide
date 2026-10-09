# RoboGuide Recovery Live Status

生成时间：2026-10-09T18:29:03+08:00

本文件由已完成运行的 `fault-verdict.json`、`fault-timeline.json` 和 `provenance.json` 白名单字段生成。它不复制原始日志、Provider 响应、凭据或聊天记录。

## 当前实验

- 目标：验证 RoboGuide 在执行前 Node loss 后的 confirmed-stop、same-owner Recovery Protocol；Task Success 单独统计。
- 当前范围：官方 `env4/task17` 机制 Pilot；不代表备用机器人接替或跨任务泛化。
- 同步工作树基线（生成前）：`f44e84857867665d5daabb59598f9e407f1a4e12`

## 正在运行

- 状态：`idle`
- 观测时间：`2026-10-09T18:25:36+08:00`

## 汇总

| 指标 | 数值 |
| --- | ---: |
| 已产生机器 verdict 的独立运行 | 7 |
| Task Success | 2 |
| Task Failure | 5 |
| Recovery Protocol PASS | 1 |
| Recovery Protocol FAIL | 2 |
| Recovery Protocol NOT_EVALUABLE | 1 |

注：clean 运行的 Recovery Protocol 为 `NOT_APPLICABLE`，不计入上述三项 Recovery 数量。

## 最新完成运行

- 运行：`gpt-6.1-sol/dog-a-recovery-1d01ef7-20261009T171500Z`
- 完成时间：`2026-10-09T17:33:03+08:00`
- 任务 / 条件：`env4/task17` / `f1-node-loss`
- 实际故障机器人：`agent 25 (quadrotor) / coherent-agent-25-generic`
- 故障动作：`[land_on] <lower livingroom floor>(1)`
- Recovery Protocol：`PASS`
- Task Success：`FAIL`
- 官方目标：`2/3`
- injection_valid / infrastructure_ok：`true` / `true`
- 实验代码 commit：`1d01ef75840484278b8abf2b56577af25f1c489a`
- 模型：`gpt-6.1-sol`

### 最近证据摘要

- 结论：Recovery Protocol `PASS`；Task Success `FAIL`。两者不得合并表述。
- 失败分类：planning/provider failure after Recovery completed。
- 事件链：`bridge_proxy_started -> fault_triggered -> local_handle_confirmed -> primary_crash_sent -> primary_exited -> recovery_required -> recovery_authorized -> same_owner_restarted -> same_owner_registered -> recovery_phase -> recovery_phase -> rebind_completed -> fault_runtime_finished`

## 最近运行

| 完成时间 | 运行 | 条件 | Recovery | Task |
| --- | --- | --- | --- | --- |
| 2026-10-09 17:33:03 | gpt-6.1-sol/dog-a-recovery-1d01ef7-20261009T171500Z | f1-node-loss | PASS | FAIL |
| 2026-10-09 11:22:32 | gpt-6.1-sol/task17-pilot6-fda825d-20261009T103000Z/06-f0-clean-rep3 | f0-clean | NOT_APPLICABLE | PASS |
| 2026-10-09 11:12:52 | gpt-6.1-sol/task17-pilot6-fda825d-20261009T103000Z/05-f1-node-loss-rep3 | f1-node-loss | FAIL | FAIL |
| 2026-10-09 11:01:35 | gpt-6.1-sol/task17-pilot6-fda825d-20261009T103000Z/04-f1-node-loss-rep2 | f1-node-loss | FAIL | FAIL |
| 2026-10-09 10:50:09 | gpt-6.1-sol/task17-pilot6-fda825d-20261009T103000Z/03-f0-clean-rep2 | f0-clean | NOT_APPLICABLE | PASS |

## 判定边界

- `Recovery Protocol PASS` 要求有序事件链、`AwaitingStop/Unknown`、可信 `Cancelled`、授权释放、Rebind，以及旧/新 Attempt 分别为 `Cancelled/Completed`。
- `Task Success` 只读取官方任务 verdict；Recovery 成功不等于任务成功。
- `NOT_EVALUABLE` 表示故障注入无效或基础设施异常，不得计为 Recovery 成功或失败。
- 本页仅汇总服务器本地已有证据；不会修改实验结果，也不会触发实验进程。
