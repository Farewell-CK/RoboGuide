# 2026-11-04 狗2 P0–P3 分阶段实测

用户确认现场具备起身、趴下及观察接管条件，公司门口路线可通行。
任务入口使用 ThinkPad Controller 的正常 Mission API，经正式 Node Protocol、狗2
Connector 和 Robonix Liaison/Pilot 执行。原生 MCP 查询只用于独立读取状态和 Scene，
本轮未从诊断脚本直接发送底盘动作或导航命令。

## 阶段结论

| 阶段 | 已取得的证据 | 尚未通过的部分 |
| --- | --- | --- |
| P0 | 两台 Thor 可 SSH，狗2唯一 Node 身份、版本和摘要已保存，Node/Connector 分别重启后重新可达，两项服务 enabled | 狗1只核对基线，尚未部署 RG；整机断电启动、Controller/Robonix 重启未做 |
| P1 | 复用已有单次站立证据；已趴下目标完成；重传返回原 handle；改变原输入被拒绝；同狗竞争任务未越过独占槽 | 本轮没有完成新的站立→趴下→站立动作组；只读任务遇到原生模型错误；未达到建议重复轮数 |
| P2 | RG 取消作用于原 session，未产生计划的本机原任务收尾为 CANCELED，Node/Controller execution 为 Cancelled；未知结果、响应丢失、进程中断由隔离协议测试覆盖 | Mission 仍为 Cancelling，缺正式安全证据及人工解除协议，原资源保持占用；真实运动取消与断链、原生重启验收未做 |
| P3 | 当前地图、定位、7项 Scene 标注和公司门口可达目标已读取 | 受 P2 进入条件限制，导航、运动取消、到达拍照及异常目标任务均未下发 |

本轮结果包含真实失败和阻塞，不能作为 P0–P3 全部通过的结论。
完整用例与进入条件见[验证计划](../physical-integration-validation-plan-2026-11-04.md)。

## 设备与部署基线

狗2为 `nvidia@100.111.156.77`，Node ID 为 `lite3-dog2`。Robonix 源码提交为
`c9c159ec2c74522f275e62fe79ec5e73e34e1028`，现场已有的源码修改保留。
狗1为 `nvidia@100.117.131.75`，Robonix 源码提交为
`1ff85de3fb1ba67a4b5c1210133e628d89a8ca29`，没有部署 RG user service。
两台机器同名，身份依据 IP、Node、独立状态目录和启动身份核对。

ThinkPad Controller 位于 `192.168.2.20`，本轮没有重启。其现有可执行文件摘要为
`670c6ad530cfcfb406db461780f6f7ddb63aab08c4053341f8caf1d421acb593`。
狗2现有 Node 二进制摘要为
`42b5b2d04e45f36d836a9b44199b82938ec429b66a1dc6738515f3b9c583dcbc`。

狗2继续使用 `rg-dog2-node.service` 和 `rg-dog2-connector.service` 两项已启用服务。
任务、相机、产物和状态在同一 Connector 组合入口。
本次分别重启 Node 和 Connector，之后 Controller 实测为 Reachable、Online。
记录的约 0.081 秒和 0.894 秒仅为本地服务检查耗时，不代表完整重连时延或上电时间。

## 本轮任务清单

| Mission | 本机结果及实际含义 | RG 观察 |
| --- | --- | --- |
| dog2-delegate-stand-20261104T075534Z | 复用此前一次 SUCCEEDED，原生确有一次 stand | 已完成的站立基线，未再次派发 |
| dog2-p0p3-read-20261104T082933Z | 状态计划 46c8-4 已完成，后续模型三次空输出导致 truncated JSON response；原会话 idle 及成功计划证据归约为 FAILED | Failed，原资源已经释放 |
| dog2-p0p3-down-20261104T083027Z | SUCCEEDED，计划 46c8-5 仅查询 state，姿态已为 down，未调用新的趴下动作 | Completed，原资源已经释放 |
| dog2-p0p3-wait-20261104T083553Z | 原任务取消，无已发出的计划，CANCELED | execution Cancelled，Mission Cancelling，原任务槽仍占用 |
| dog2-p0p3-wait-20261104T083958Z | 同狗竞争目标停在 Ready，原生 journal 无对应接纳 | 零 attempts，经正常 Mission cancel 收尾为 Cancelled |

重传实验使用旧站立 attempt 的完全相同请求。HTTP 返回 200、原 handle
`6c266142-210c-4c4f-a580-9f299ef3d67d` 和原 SUCCEEDED。改变目标但保留身份得到
409 `attempt input changed`。实验前后本机 runs 均为 6，events 均为 83。
本次验证未产生新的站立任务。

## P2 阻塞的原始身份与原因

原取消任务对应本机 handle `ce45330a-6d39-4b64-b658-d554c53d3376`，
session `0ccc6591-38c7-4633-9334-d15237a8a64d`，
请求 task_id `dbc5da88-4e51-4eb3-aba4-21d040913f44`。
取消通过 RG execution cancel 入口进入，未创建替代原生任务。

Controller 的 safety-stop 记录为 `manual_escalation`，原因是
`operation safety declaration or current session is unavailable`。
该记录保留了原 session，但 operation safety support、独立状态 export 与证明均为空。
当前部署缺少 `roboguide.execution-safety` 声明和对应的 safe-state State 导出。

原请求时间为 1793781356302 ms，原 deadline 为 1793781366302 ms，generation 为 1。
原任务槽 `dog2-pilot-task-slot` 保持 Bound，mission recovery 明确报告
`new_execute_fenced=true`、`ownership_retained=true`、`review_due=true`。
这证明任务终止回传已到达，仍缺少全局资源释放所需的独立安全依据。

现有 Controller 没有该人工状态的正式对账解除入口。新增声明不能补写旧 admission，
原截止时间也不能重置。本轮没有直接修改 Controller SQL、清空 journal、替换资源 ID
或创建新 Node 绕开原占用。该任务继续保留待处理状态。

## 本轮代码修复及部署范围

1. 接收带原生前缀的模型解析错误，保留实际 RPC 详情。
2. 规划错误发生在已成功结束的计划之后时，要求原 provider、原 session、原 turn、
   完整计划覆盖、每个根节点成功及 scoped idle 回复，才结束为 FAILED。
   失败、取消、跳过或缺失根结果继续 UNKNOWN。
3. 取消发生在任何计划发出之前时，使用原 turn 的 interrupted 事件归约本机
   CANCELED。已经出现计划时，同一事件仍不能证明终止或停止。
4. 通用 Node 对原 handle 的只读状态观察在连续失败达到 UNKNOWN 门槛后继续以有界
   退避查询。原 Execute 不重放，原身份、资源锁和 deadline 均保留。

前三项已部署到狗2 Connector。第四项是本分支源码修复，现场 Node 二进制保持上述
基线，尚未进行新 Node 二进制的真机更新与故障验收。

## P3 只读预检

17点前的观察确认地图为 `1101_visual_dog2`，mapping 处于 localization，定位可用。
Scene 返回 7 个标注，公司门口 region 为 `scene.region.anno.2637f95d`，
`reachable=true`，目标为 x=7.6304692309349775、y=-2.732866508886218、yaw=0。
这些是目标解析结果，不能作为实际通行、导航到达或物理停止证明。

16:57 的预检姿态为 down、电量 30%，state age 为 6 ms、stale=false。
17:08 的收尾只读观察仍为 down、电量 21%，state age 为 1 ms、stale=false。
当前本机导航有效限速仍为 60% × 0.8 = 0.48 m/s，本轮没有改写速度。
后续导航目标要求不高于 0.2 m/s，须由 Robonix 实际执行并报告所采用的限制。

## 证据和后续进入条件

ThinkPad 原始 Mission、HTTP 观察及最终投影保存在
`~/rg-lab/p0-p3-20261104/`。Thor 部署、重启、重传和原生 runs/events 导出保存在
`~/rg-lab/delegate-20261104/p0-baseline/`。Windows 副本位于工作区的
`lab/lite3-rg-p0-p3-20261104/`，原始运行数据不加入源码仓库。

Windows 与 Thor ARM 的 29 项隔离协议测试通过，覆盖真实 loopback gRPC/HTTP、
响应丢失、取消、进程中断、重启身份恢复和事件容量。其中计划已发出的 interrupted、
取消根与缺失结果均作为拒绝自动收尾的反例。Thor 子进程测试须显式设置包根 PYTHONPATH。
Ruff、格式、函数文档和生产模块 strict mypy 检查通过。

Node Service 的 139 项库测试与 all-targets Clippy 在 WSL 现有 Rust 1.98 工具链通过。
首次全量测试发现一项既有 Habitat 测试仍要求全部 attributes 为空，而已提交的部署
明确声明了 floor access。测试修正为核对实际楼层约束，并单独保证 max-payload-grams
继续缺省。本轮没有改动该场景部署。仓库固定的 Rust 版本仍为 1.99.1，未改写工具链声明。

下一步先补齐 Robonix 对原任务最终终止和设备安全状态的可查询证据，并通过 Node
现有安全状态通道供 RG 观察。Robonix 仍负责本机导航、动作及即时安全，RG 负责原任务
与资源义务的闭环。原人工占用需要独立、可审查且保留历史的正式对账处理。
这些条件通过后，再执行低速公司门口导航、运动取消、采图和异常地点测试。
