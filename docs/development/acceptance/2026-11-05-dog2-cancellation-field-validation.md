# 2026-11-05 狗2取消与恢复现场验收

本轮用户明确选择“先只做无动作验收”。全部新任务经 ThinkPad RG Controller、正式
Node Protocol、狗2 Connector 和 Robonix Liaison/Pilot 进入。没有下发站立、趴下、
速度修改或导航任务，也没有升级现场 Robonix。独立诊断仅查询状态及活动计划。

受检实现为 `8fd00c4d8de3673de37bedd93af99209235fd275`，依据
[ADR-0232](../../decisions/0232-task-cancellation-settlement.md)。
此前的软件测试、迟到终态修复及权限边界见
[2026-11-04 软件验收](2026-11-04-task-cancellation-settlement.md)。

## 本轮结果

| 验证项 | 实际结果 |
| --- | --- |
| 狗2开机恢复 | 新 boot ID 下原 Node、Connector 均自动启动，RG 收到 Reachable/Online；初始电量约 97% |
| 修复版部署 | ThinkPad Controller、Thor ARM Node、同进程 Connector 与声明均更新，继续使用原存储和任务 journal |
| 原人工占用结案 | 原取消 Mission 通过正式认证管理员接口变为 Cancelled，资源归零，原停止记录及截止时间完全相同 |
| 认证与幂等 | viewer 的结案请求为 403；同一管理员命令重传返回相同原回执 |
| 新规划期取消 | 3 个独立 Mission 各确认原生 turn 后由 RG 取消，均为 Cancelled、0 计划、0 占用 |
| 正常只读任务 | Completed；唯一原生调用为 `robonix/primitive/chassis/state`，返回 down、电量 91% |
| Connector 与 Node 分别重启 | 原 native runs、事件、身份和序列逐项相同，无任务重放 |
| Controller 冷进程重启 | 4 项取消与 1 项完成结果保持，原 stop purposes 完全相同，资源仍为零 |
| 收尾状态 | RG ready=true、unknown_attempts=0，狗2 Reachable/Online，状态导出新鲜，原生活动计划为零 |

这些结果验证 P2 中无副作用取消、管理员结案及终态后的服务恢复子项。
运动取消、物理停止、原生 Robonix 重启查回、执行中断链和多参与者验收尚未完成。
P3 导航本轮未执行。

同日随后另行授权的站立复验未通过，新增了需要保留的未知执行占用。
当前现场状态应同时参考[站立复验记录](2026-11-05-dog2-stand-field-validation.md)，
不得把本轮无动作验收的零占用状态当作该次动作后的状态。

## 部署身份与保存的数据

Controller 位于 `duchaoke@192.168.2.20`，Operator 入口为本机
`https://127.0.0.1:8080`，使用真实 mTLS 证书与版本化角色映射。
Node Protocol 和 Artifact 原有 mTLS 配置保留。
管理员 principal 为 `field-operator:33457`，其身份从证书授权取得。
私钥仅保存在 ThinkPad 的权限受限实验目录，原始环境文件没有导出到仓库或报告。

狗2为 `nvidia@100.111.156.77`、Node `lite3-dog2`，本轮 boot ID 为
`a819edf1-195f-43fe-b8dd-8534821a3b6b`。原生 Robonix 源码仍为
`c9c159ec2c74522f275e62fe79ec5e73e34e1028`，现场已有修改保留。

| 部署物 | SHA256 |
| --- | --- |
| Controller release 二进制 | b08e1f5b6433010ec62f5cc9c3447f8c7b9070b8dda0e129bec9833ca411dd80 |
| Thor ARM Node 二进制 | e6ce294a78934f167d4f90e8acee6843497b30d7d32eb52703b8df99aa9499bb |
| 组合 Node 配置 | e15ae97df565e516367f0344458ef8eef302edf9cc3422278fc6f981a67da330 |

新配置为 node-config/v0.13，实际协商 Node Contract v0.12。`dog2-pilot` 声明
execution-safety/v0.2，并通过 `pilot-task-effects` 导出 observed safe-state/v0.4。
相机、状态与任务继续由同一个 Connector 组合入口提供。

Controller、Node 与 Connector 现在均为 enabled 的用户服务，两台主机 Linger=yes。
ThinkPad 新增 `rg-controller.service`，狗2沿用原两项服务名称。Controller 仍使用
`~/rg-lab/lite3-20261102/state/controller.sqlite3`，Node 和 Connector 沿用各自原 journal。
切换前保存 SQLite 一致性备份、原 unit、配置、程序摘要与原任务事件。
本轮没有删除历史、改写 SQL 任务状态、替换资源 ID 或新建 Node 绕过原占用。

## 原任务正式结案

原 Mission 为 `dog2-p0p3-wait-20261104T083553Z`，对应 handle
`ce45330a-6d39-4b64-b658-d554c53d3376`、native session
`0ccc6591-38c7-4633-9334-d15237a8a64d`、task
`dbc5da88-4e51-4eb3-aba4-21d040913f44`。

独立只读核查确认原 turn、provider、session 和请求摘要一致，原 5 项事件含
interrupted，完整已保存任务流无 plan、node_state、batch_result 或 FinalText。
原本机状态为 CANCELED，RG execution 已为 Cancelled，Mission 保持 Cancelling。
核查以原持久事件为依据，没有把设备本次断电或新声明当成原任务的无副作用证据。

通过 `POST /v1/executions/{id}/safety-stop/reconcile` 提交一次
`field-dog2-no-effects-20261105-v1`，随后重传相同命令核对原回执。
原 record digest 为
`sha256:99c5b53c3811f432c981c39bd11ad9a40732f7c2171b06f9862b9c7edda1b0cd`。
原 requested_at=1793781356302、deadline=1793781366302、generation=1 及
ManualEscalation 记录均保留。独立 settlement 记录管理员责任和核验内容。
回执明确 physical_stop_confirmed=false、automatic_continuation_authorized=false。

## 三次新任务取消

| Mission | Native session | RG 接收取消至完成结案 |
| --- | --- | --- |
| dog2-field-cancel-20261105T022247Z | 4b4bc396-e986-4648-8ac2-828749dc6040 | 947 ms |
| dog2-field-cancel-20261105T022338Z | 8e8ffb80-f438-49ca-b2e4-def3a4ba5bf0 | 926 ms |
| dog2-field-cancel-20261105T022357Z | 47778fc9-7ffa-42d9-9e5b-bb002ce7d8ab | 3096 ms |

每项均使用独立 Mission、attempt、session 和原始预算。先通过只读 journal 观察到
原 turn_id，再调用正常 Mission cancel，取消没有绕过 RG。每项原事件均为 5 项，
无已发出的计划，原任务各完成一次 scoped abort。新 invocation 实际携带 generation=1。
Controller 消费原任务的 operation-owned State、原 Cancel receipt 和终态后产生
automatic-no-effects settlement。三项均未设置 confirmed_at，也未产生设备停止证明。

正常只读 Mission 为 `dog2-field-read-20261105T022448Z`，session 为
`e5fc0f58-4ec9-4861-ad77-ca8450a2a544`。计划 `1294-1` 的唯一叶子调用为
`lite3_chassis` 的 chassis/state，参数为 `{}`。完整 13 项原事件包含任务 done、
成功计划根和 FinalText，RG Mission 为 Completed。

## 重启与收尾

Connector 和 Node 重启命令耗时约 0.54 秒和 0.045 秒，仅表示本机命令耗时。
后续 Controller State 中实际观察到新 session 的新鲜导出，作为重连证据。
Controller 冷进程重启至接口与原状态复核约 32.93 秒，5 项结果、4 项完整 stop purpose
及零资源占用均保持。所有服务重启后，原生 journal 仍为 10 条 runs、111 项 events，
所有原记录逐项相同，原生活动计划 count=0。此处没有进行整台 ThinkPad 或新版本狗2的
再次断电上电，也没有重启原生 Robonix。

最终 RG 返回 ready=true、degraded=false、unknown_attempts=0；狗2为 Reachable/Online。
robot-state 和 pilot-task-effects 在新 Controller session 下均为 stale=false。
10:27 左右的状态导出电量约 90%；最后一次经 RG 的姿态查询为 down。

## 实际失败、检查与证据

首次切换使用先前已受检的 debug 二进制。在完整恢复约 484261 项历史事件后，
Operator 权限配置拒绝了 viewer 的全局租户范围。该配置错误保留在 journal 中，随后
将 viewer 缩小到 default 租户，没有放松产品权限检查。debug 首次恢复约消耗
8 分 31 秒 CPU。随后以相同冻结源码构建 release，并在独立软件进程中重新通过自动取消、
管理员结案、冷重启及原旧 writer 拒绝门禁，再部署该 release。完整历史始终保留。
大历史下 debug 冷启动开销列为运行限制，不将软件夹具启动耗时当作现场恢复时延。

Thor ARM 上 31 项协议测试通过，新 Node 对组合配置的实际离线 conformance 通过。
516 项 Rust 回归、Clippy 和迟到终态反例沿用前一受检提交的证据。
今天新增的三次取消、正常只读任务、角色拒绝、原命令幂等和服务恢复均有独立现场记录。

Windows 的原始非敏感证据位于工作区 `lab/rg-field-cancellation-20261105/`；
ThinkPad 和 Thor 的部署与备份位于各自 `~/rg-lab/cancellation-20261105/`。
版本、配置和完整原事件分别保存，证书私钥、原环境和运行数据库不进入源码仓库。
运动终止和物理安全证据仍需后续接通 Robonix 的原任务查询并独立验收。
