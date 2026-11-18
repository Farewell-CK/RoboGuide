# 2026-11-05 狗2姿态闭环、Mission 重复与单槽竞争

用户授权开始下一轮验证，现场随后分别确认“已站稳，可以继续趴下”和“已趴稳”。
全部任务通过 ThinkPad RG Controller、正式 Node 与 Robonix Pilot 执行；诊断只读。
本轮没有导航、移动、底盘 stop 或跨系统直接动作调用。

## 本轮通过的现场用例

| 用例 | 原生调用 | 结果 |
| --- | --- | --- |
| 已趴下时保持目标 | 一次 chassis/state | down，目标满足，无姿态动作，Completed |
| 趴下到站立 | state → 一次 stand → state | down → standing；现场确认站稳，Completed |
| 站立到趴下 | state → 一次 down → state | standing → down；现场确认趴稳，Completed |
| 重复原 Mission 请求 | 同一计划再次提交正常 RG 入口 | 返回原受理回执，原 attempt 与本机 handle 保持，没有再次派发 |
| 同 Mission 改变只读目标 | 相同 ID、不同 objective | HTTP 409，原 Completed 与资源状态保持 |
| 同狗两个只读任务竞争 | 两项独立 Mission，各一次 chassis/state | A 占用时 B 为 Ready；A 结束后 B 才入本机，均 Completed |

本轮共 5 项新 Mission，原生事件分别为 13、24、23、12、13 项，合计 85 项。
每项均为 SUCCEEDED，cancel_requested=0、abort_sent=0。只有一次 stand 与一次 down。
初始底盘查询为 down、电量 87%；实际姿态任务查询分别约为 86%、85% 和 83%，
只读竞争任务均约 80%。这些是各任务当时的观测值，不是持续电量保证。

### 原始 Mission 身份

- 保持趴下：`dog2-p1-hold-down-20261105T073950177181Z`。
- 站立：`dog2-p1-stand-20261105T074034319532Z`。
- 趴下：`dog2-p1-down-20261105T074233068583Z`。
- 竞争 A：`dog2-p1-read-20261105T074546826977Z`。
- 竞争 B：`dog2-p1-read-20261105T074548657755Z`。

各任务的原 handle、session、task_id、plan/call 与原始字节均保存在 native-summary
及独立 Mission 目录，没有覆盖之前的失败或手工结案记录。

## 重复请求与竞争的具体证据

重复保持趴下的原 Mission 返回 HTTP 202，body 为最初受理时的 Running 回执；随后
GET 原 Mission 仍为 Completed，execution-attempts 逐项相同。它不是新执行结果。
原生 journal 中该 Mission 始终只有 handle `9481041b-1f3c-4e7c-9d00-397575fd8851`，
状态 SUCCEEDED、序列 17。改变目标的重复请求返回明确的不同 plan/Group 冲突。

竞争实验先只读观察到 A 原 turn_id 已受理，再提交 B。14 项跨 Controller/Thor 观察
记录中，dog2-pilot-task-slot 占用始终不超过一项，本机未决 run 始终不超过一项。
首项记录为 A 的 task Active、B 的 task Ready，唯一占用归 A。B 的本机 created_ns
晚于 A 完整原生终态事件的 received_ns，实际原生执行串行。A、B 分别只有一个 handle
与一项只读计划，最终都 Completed，资源归零。

本轮现场覆盖 Mission 请求重传与冲突。Node attempt 报文重传继续沿用隔离协议证据，
尚未通过现场的响应丢失注入重新验证，不能把两者混为同一层验收。

## 新启动及后续进入条件

本轮 boot ID 为 `fc18215e-37eb-4ddf-9992-a343836fc336`，两项狗2用户服务均为
active/enabled，原 v2 journal 可正常恢复，历史未决为零。观察到 Thor 新启动后重新
上线；没有据此推定狗的运动主控也经历了同一次断电。

5 项新任务后原生 journal 为 20 条 runs、284 项事件、2 项本机管理员回执，未决为零。
Controller 升级前另外冻结 9 项原 Mission/attempt、2 项完整 stop purpose 和原生表摘要，
用于独立核对后续就绪探针修复部署。部署结果在其独立验收中记录。

P1 本轮上述用例通过，不代表所有建议轮数或 P2/P3 均已完成。运动取消仍缺少本机
最终终止与停止观察，具体真实源码依据和进入条件见
[运动停止接入缺口](../robonix-motion-stop-integration-gap-2026-11-05.md)。

原始非敏感证据位于工作区 `lab/rg-p1-validation-20261105/`、Thor
`~/rg-lab/p1-validation-20261105/` 和 ThinkPad
`~/rg-lab/cancellation-20261105/p1-validation-20261105/`。
