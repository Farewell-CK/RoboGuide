# 2026-11-09 狗2姿态 State 接入与遥控器连接后的重试

## 当前已验证内容

RG 已通过正常 Node State 接入 Robonix 报告的底盘姿态。统一 Connector 在原观察线程
只调用固定 chassis/state，不创建 Pilot 任务；中央读取器仅 GET Controller 的 inventory
和 State，校验来源、会话/序号、Controller TTL、原生 aggregate age 及保留时间。
现场正常读取、真实采集服务停止后的过期拒绝和恢复均通过，原生 journal 无新增任务。

动作部分使用用户重新授权后的新 MI Request，保留前次 Failed。尚未在本记录中将
规划成功或原接口 standing 文本当作实际站立成功；动作结果及现场反馈另行补充。

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

## 证据与分支

代码见 ADR-0239、chassis_state.py、posture.py 及原 combined Connector。
本地证据：C:/workspace/DEAIOS/lab/rg-dog2-mi-remote-retry-20261109。
狗2公开部署记录：~/rg-lab/rg-posture-state-20261109。
ThinkPad 读取器及原始 State 证据：~/rg-lab/rg-posture-state-20261109。
MI 新请求记录：~/rg-lab/dog2-mi-remote-retry-20261109。
私钥、模型密钥及私有环境配置没有复制进公开证据或仓库。
