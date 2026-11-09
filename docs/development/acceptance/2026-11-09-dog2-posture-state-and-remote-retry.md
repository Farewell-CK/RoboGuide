# 2026-11-09 狗2姿态 State 接入与遥控器连接后的重试

## 当前已验证内容

RG 已通过正常 Node State 接入 Robonix 报告的底盘姿态。统一 Connector 在原观察线程
只调用固定 chassis/state，不创建 Pilot 任务；中央读取器仅 GET Controller 的 inventory
和 State，校验来源、会话/序号、Controller TTL、原生 aggregate age 及保留时间。
现场正常读取、真实采集服务停止后的过期拒绝和恢复均通过，原生 journal 无新增任务。

动作部分保留前次 Failed，通过真实 MI 新请求完成一次站立：Robonix 原始状态从
down 变为 standing，RG State 独立于该任务回执的周期观察也报告 standing，用户现场
确认“已站稳”。随后用户另行授权趴下，只调用一次 down，但原生实时状态与 RG State
均仍为 standing；用户反馈“仍然站着，没有趴下”“原地踏步了一下”。趴下物理目标未达成。
本轮末尾 ready=true、Unknown=0、allocations=[]，没有重试或待处理动作任务。

## 授权与状态变化

用户说明，上一轮没有起身是因为遥控器未连接，机器狗进入阻尼，现已处理并要求重试。
这是用户提供的现场解释，不是 RG 从该状态接口读到的模式证明。本机原接口不返回
遥控器连接或阻尼字段，本轮不自行更改模式或绕过 Robonix 发送硬件命令。

期间用户询问是否当前已站立、是否先趴下再起身。通过 RG 的只读 Task
dog2-current-posture-20261109T025310Z，Robonix 原生 plan 9890-10 仅查询状态，
返回 standing、74%、stale=false、age_ms=2。该 Task Completed，没有动作调用。
后续接入新观察源时实时报告已变为 down、67%/66%。本轮没有在两次观察之间下发姿态
切换，不能据此确定姿态变化由谁或什么机制触发。既然最新 RG State 为 down，
后续只安排重新授权的单次起身，不额外下发一次无必要的趴下命令。

## 状态通路与边界

```text
Robonix 已有 chassis/state（固定只读 MCP 工具）
  → 同一 Connector 的可选周期观察，Atlas 发现并绑定原 provider
  → 缓存原数据、原 aggregate age、请求时间、provider registration
  → 原 GET /v1/state，新增 chassis 观察字段
  → 原 Node robot-state export / lite3.robot-state/v0.1
  → 原 Controller State
  → RG posture 读取器：只读 inventory + 精确 State 来源 + 双层时效检查
```

RG 不从关节角自行推导姿态，不让 Pilot 每次规划状态查询。动作仍通过正常 MI、
Controller、Node 及 Pilot 委派链路执行。姿态读取不增加动作能力注册或新进程。
dog2 的 Node 配置无需修改；仅在原统一 Connector 追加 --chassis-state 并重启该接入
服务，没有重启 Node、Robonix 或狗1。默认部署不启用此观察。

原数据只有聚合 ts/age/stale，不能证明 posture 自身的独立测量年龄；网络传输单程年龄
也未单独测量。读取结果保留这些限制及 motion_authorized=false。它是归属明确的报告，
不是动作许可、物理效果独立验证、硬件可信证明或物理停止证据。缺失、未知、重放老值、
时钟回退或来源不匹配不得提升为当前姿态。此 additive observation 不是 MI World admission。
本次实验的审批脚本明确调用该 RG 读取器核对当前状态；没有声称所有 Controller 任务
已自动具有通用姿态准入规则。

## 软件与真实现场验证

- 63 项 integration unittest 通过，含真实 loopback MCP 请求、唯一 state 工具调用、
  HTTP session/Atlas channel 清理、cache 过期/时钟回退，以及中央旧/未知/外来源拒绝。
- 包严格 mypy、Ruff 和函数文档检查通过。
- 初次现场试读发现 Atlas 返回 /mcp/，实际服务 307 到 /mcp；固定本机 endpoint
  规范为 /mcp，仍拒绝通用重定向。没有开放任意工具或任意远程 URL。
- 仅通过 Controller 读取 node:lite3-dog2/robot-observer、robot-state、robot object，
  报告 down、66%，有原 source_epoch、sequence 及原数据。
- 停止原 Connector 12 秒后，RG reader 返回 unavailable、posture=null，原因为
  controller_state_stale_or_unqualified；重新启动后恢复 reported/down。
- 接入及真实过期试验前后 Pilot journal 均为 33 runs / 569 events，逐表摘要相同，
  没有新增原生 Task/Plan，也没有 Node 或 Robonix 重启。

## 真实 MI 站立重试

最初 Request request-70471a12dedc47fdbc8bffc0003c7cfc / Mission
mission-0bb2e1398300414e9014cf11c6a9a82d，首轮 Planner 改动冻结目标，被一致性校验
拒绝，未派发。正常 /messages 转入下一对话后，经 Reviewer/Repairer 二稿通过。
审批前 RG 读取器核对当前姿态与唯一 task.delegate 候选为狗2，MI 提交原计划。
该任务原生只调用 state 和 read_capability_doc，未调用 stand；Pilot 因未找到额外
动作许可字段而选择了“停止并报告”分支，原生 done、RG Completed。
此 Completed 不是起身成功，本机接口也没有声明所谓 stand_allowed 字段。

新的实际动作 Request 采用明确本机目标及已确认授权，由 Robonix 自身接口处理
动作拒绝，不要求它查找不存在的额外许可字段。没有改写旧任务或本机实现。

| 字段 | 站立实际记录 |
| --- | --- |
| Request | request-788db827b6d04a22925343087b683e60 |
| Mission | mission-eb543d2500dd4bfb900fa15ddc9f271a |
| Task / Role | stand-in-place / dog2-role |
| Attempt | attempt-46:group-mission-eb543d2500dd4bfb900fa15ddc9f271a-14:stand-in-place-9:dog2-role-1 |
| Native session | 49a7392f-7e7d-41c0-8b95-85b3bc373878 |
| Native request/turn | 0f739c90-1930-41e0-bf54-1db26efcc5d6 |
| Native plans | 9890-15、9890-16 |
| 调用顺序 | chassis/state → chassis/stand → chassis/state |
| 起始状态 | down、63%，stale=false、age_ms=4 |
| stand 回包 | status=standing、posture=standing |
| 动作后原生状态 | standing、63%，stale=false、age_ms=1 |
| RG 周期 State 复查 | standing、62%，源为原 robot-state |
| RG / Native | Completed / done |
| 现场反馈 | 已站稳 |

此动作 Request 实际执行 Interpreter、Planner、Reviewer 各一次 gpt-6.1-sol 调用，
记录原始 response_id 及摘要。前一 Request 含原失败/新对话共 7 次模型调用，两者
均未出现 provider 错误。MI 本轮仍使用原 World/Memory grounding 策略，node-class
的姿态报告没有被无条件提升为 MI World；审批前显式运行了 RG State 读取器。
仅一个 Actor、Task、Role，independent，一个 Space 槽；不声称双狗协作或 Handoff。

## 另行授权的趴下：执行结束但姿态目标失败

用户确认站稳后明确要求“再下发一个趴下指令”。这次使用原结构化 MissionPlan HTTP
入口，未重新运行 MI，不冒充另一次自然语言规划。目标仍通过 RG admission、
Matching、Control、Commit/Bind、Node、统一 Connector、Liaison 交给 Pilot，
没有在助手侧直调底盘或在 RG 注册 down 原语。审批/下发前由 RG 读取当前姿态。

| 字段 | 趴下实际记录 |
| --- | --- |
| Mission | dog2-down-after-mi-stand-20261109T031448Z |
| Task / Role | down-in-place / delegate-role |
| Attempt | attempt-47:group-dog2-down-after-mi-stand-20261109T031448Z-13:down-in-place-13:delegate-role-1 |
| Native session | c9e5e5ac-9e60-4f67-9cc0-9e33dc04796c |
| Native request/turn | f30412df-ceda-42cc-9a77-b0132fe57c16 |
| Native plans | 9890-18、9890-19 |
| 调用顺序 | chassis/state → chassis/down → chassis/state |
| 动作前 | standing、60%，stale=false |
| down 回包 | status=down、posture=unknown |
| 动作后原生状态 | standing、60%，stale=false、age_ms=1 |
| RG State 复查 | standing、59%，源/会话/序号与时效通过读取器检查 |
| 原生任务 / RG 生命周期 | done / Completed |
| 现场反馈 | 仍然站着，没有趴下；原地踏步了一下 |

Pilot 将“按限制停止、不重试并报告无法确认”的分支标为 done，RG 依本轮既定
execution-report basis 归约 Completed。**这不能证明趴下目标已达成。** 状态读取器
确实检测到 standing，但当前任务未配置目标姿态的独立 satisfaction verifier，
Controller 没有据此把 Mission 改为 Failed。保留原状态，不事后篡改历史。
需要在后续正式目标校验设计中区分“本机流程结束”与“所需姿态满足”，不能让运维
脚本的比较被描述成通用 Controller 准入/终态校验规则已实现。

本轮只调用一次 down，之后没有重发、调用 stop、其他姿态或移动命令。现场原地踏步
属于操作者观察到的非预期效果，不据此推断原生 down 实现具体原因。没有修改 Robonix。
最终 Pilot journal 为 36 runs / 641 events；本轮前 32/555 条原记录完整摘要不变。
增加的四个任务分别是姿态只读查询、未执行动作的条件分支、实际单次站立、实际单次趴下。
资源与 Unknown 均为零，不将资源释放解释成物理停止或本机动作效果证明。

## 证据与分支

代码见 ADR-0239、chassis_state.py、posture.py 及原 combined Connector。
本地证据：C:/workspace/DEAIOS/lab/rg-dog2-mi-remote-retry-20261109。
狗2公开部署记录：~/rg-lab/rg-posture-state-20261109。
ThinkPad 读取器及原始 State 证据：~/rg-lab/rg-posture-state-20261109。
MI 新请求记录：~/rg-lab/dog2-mi-remote-retry-20261109。
私钥、模型密钥及私有环境配置没有复制进公开证据或仓库。
实际证据另含 action-request-result.json、action-approval-result.json、
action-execution-result.json、action-native-result.json、action-posture-confirmed.json、
down-plan.json、down-final-controller.json、down-native-final.json、
onsite-stand-confirmation.json、onsite-down-observation.json、final-summary.json 及 SHA256SUMS。
