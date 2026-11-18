# 2026-11-05 狗2站立、返程及趴下

用户授权“先让狗站起来，然后再让狗回到起点”，返程后现场确认“已回到起点并停稳”，
并追加授权原地趴下。三个独立 RG Mission 均为 Completed，本机原 Task 均为 SUCCEEDED。
实时姿态最终为 down，RG 占用与 Unknown 为零。但返程本机报告存在任务结束与目标
达成的语义差异，不能把本轮记录解释为自动目标判定一致性已经通过。

## 顺序和本机执行

所有动作仍经 RG Controller → Node → 同一 Connector → Robonix Liaison/Pilot/Executor。
没有直接调用底盘 SDK，没有修改 Robonix 内部实现。前一 Mission 完成且独占槽释放后
才提交下一 Mission；每项只发布一次。目标地图为 `1101_visual_dog2`，原起点为
`(0.7672304440790438, 0.448885835803414)`，yaw 为 `-0.23701874981410953`。

| 步骤 | Mission | 原生事件数 | 原生创建至末事件 |
| --- | --- | --- | --- |
| 原地站立 | `dog2-stand-before-return-20261105T092626Z` | 23 | 26.014 秒 |
| 返回起点 | `dog2-return-after-stand-20261105T093030Z` | 46 | 245.242 秒 |
| 原地趴下 | `dog2-down-after-return-20261105T093930Z` | 24 | 49.110 秒 |

站立只有一次原生 stand 调用，返回 standing；另一次只读姿态查询确认 standing，
age 为 1 ms、stale 为 false 后才提交返程。返程前原生只读限速确认为 session 20%，
等效上限 `0.16 m/s`。站立和趴下均没有导航调用，趴下只有一次原生 down。
趴下后的独立只读查询为 down，age 为 3 ms、stale 为 false。

本轮前，上一条[换电返程的超时占用](2026-11-05-dog2-return-after-battery.md)通过已有
本机离线管理员工具及 Controller mTLS 管理员接口正式取消结案。当前实际活动计划
为零的观察、原行和完整事件摘要、原 scoped abort 及新授权分别保留。该处理没有
制造 no-effects、物理停止证明或自动续跑授权；本轮使用新的 Mission 和原生身份。

## 返回现场结果与回执差异

RG 只发布了一次返程 Mission，只有一个原生 handle
`41f89d67-9d0e-49bf-ae25-6fe690c3b19d`，session 为
`08066b18-1763-4de2-af65-5fb91972867b`，turn 为
`822aa01d-cca8-48ed-a242-bbebe0e72d90`。

Robonix 在该 Task 内对同一地图目标发起了三次 navigation/navigate，call_id 为
`432e-7:0`、`432e-8:0`、`432e-9:0`，均返回 SUCCEEDED、recoveries 为零，分别报告
distance_remaining 为 0.184、0.253、0.254 m。这是同一本机任务内的重复导航调用，
不是 RG 重传 Mission 或重新派发 attempt。最终 map 位置约 `(0.674510, 0.638510)`，
yaw 约 `-0.0846`；以记录坐标计算平面几何误差约 0.21 m。原生距离字段保持原值。
现场用户确认已回到原先位置并停稳，该观察独立于软件终态保存。

原生 Task 的结构化状态最终为 done，成功标准却表述为取得导航结果与最终位姿，
未到目标时报告未完成；status_reason 与 final_text 均表示目标到达未获确认。
当前适配器依结构化 done 回传 SUCCEEDED，RG 因而进入 Completed。这表明“委派工作
结束”和“原目标已经达成”的语义尚未对齐。应保留结构化状态、原文、实际位姿和现场
观察，不能单凭 Completed 宣称物理到达，也不以自然语言关键词改写原生终态。
结果合同的后续完善需要独立处理，本轮未修改状态归约逻辑。

准备收尾时再次 GET 已发现返程 Mission 为 Completed，因此没有发送取消请求。
上述三项新任务的 cancel_requested 和 abort_sent 均为零；全部最终占用、Unknown
和实际 Executor 活动计划均为零。没有下发其他目的地或额外姿态动作。

## 证据与范围

ThinkPad 的 `~/rg-lab/cancellation-20261105/stand-return-retry-20261105/` 及三个原
Mission 目录保存了计划、验证、一次发布、状态和管理员回执。Thor 的
`~/rg-lab/stand-return-retry-20261105/` 保存原生行、事件字节、现场确认与只读姿态。
公共证据包另存于工作区 `lab/rg-stand-return-20261105/`，含逐文件 SHA256 清单；
SQL 备份保留于独立 private 目录，不进入公共包。

本轮验证单狗的顺序委派与现场动作，未验证运动取消的物理停止证据、自动交接或
多参与者协作。返程原文与结构化终态差异保持为未解决项。
