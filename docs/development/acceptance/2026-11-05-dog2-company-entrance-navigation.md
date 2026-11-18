# 2026-11-05 狗2公司门口导航到达

本轮经用户明确授权及现场监护，通过 RG → Node → 同一 Connector → Robonix
Liaison/Pilot/Executor 完成单次导航。RG Mission 和原 attempt 均为 Completed，
Robonix 原 Task 为 SUCCEEDED；用户确认“已到公司门口并停稳”。

## 原任务与目标

- Mission：`dog2-nav-company-entrance-20261105T082532Z`。
- 原生 handle：`cb2010e1-f5fe-4ff5-b518-89cb87d0ef5c`。
- Session：`44b3af6b-1c7f-4548-87fd-5e3cabe20c1f`。
- 原 turn：`84616d1b-6a61-4714-beb3-a4ec26c69713`。
- 地图 `1101_visual_dog2`；公司门口区域 `scene.region.anno.2637f95d`。
- Robonix Scene 解析的可达区域内目标为 `(7.630469, -2.732867)`，yaw 为 0。
  出发前实际 map 定位为 `(0.767230, 0.448886)`，yaw 约 `-0.237019`。

只读预检 Mission `dog2-nav-preflight-20261105T081545Z` 已完成。其空 run_id 的导航状态
查询返回 unknown，未将此解释为空闲证明。原 Executor 活动计划为零；Scene 补充查询
确认当前地图、定位和目标可达。一次 ROS 状态读取超时没有提供无活动目标证明；该
自有只读查询进程已清理。导航接纳及执行决策仍交给 Robonix。

## 实际执行与结果

Robonix 先读取导航能力、目标、定位、姿态和限速；把 session 导航上限设为 20%，
只读确认等效 `0.16 m/s` 后，执行一次起身及一次导航。没有地图切换、探索、趴下、
返回、重复导航或其它目的地动作。该速度设置在本次任务结束后继续有效。

原生共 41 项事件，sequence 为 45，Task 从创建到最后原事件约 246.232 秒。
导航调用从 16:26:32.442 至 16:29:20.523，约 168.080 秒；这包含导航处理，
不能将其全部归为连续行走时间。导航结果为 SUCCEEDED，报告剩余距离 0.331 m、
recoveries 为 0。最终新鲜 Scene 定位 `(7.399055, -2.593617)` 属于公司门口区域。

结束后 RG 资源占用为零，ready 为 true、Unknown 为零，原生活动计划为零。
现场到达确认单独保留。Controller 已使用
[readiness 修复部署](2026-11-05-controller-readiness-writer.md)。

## 速度反馈与范围

用户反馈“行走速度慢”。本轮主动采用了 `0.16 m/s` 的速度上限，巡航观察中的部分
里程计速度约为 `0.05–0.07 m/s`；离散、带缓存的样本不能代表整段峰值或长期性能。
建议下一次独立对照可评估 40% 即 `0.32 m/s` 上限，但尚未应用该建议，也不能把
上限翻倍解释为实际速度必然翻倍。

本记录只验收到达子用例，没有注入运动取消或失联故障，不通过 P2、自动恢复或交接。
缺口仍见[运动停止接入说明](../robonix-motion-stop-integration-gap-2026-11-05.md)。
用户随后授权的返程因电量耗尽中断，换电后的新返回任务又在规划阶段超时；失败、
取消和未决占用另见[返程记录](2026-11-05-dog2-return-after-battery.md)，不计为返回成功。

原始非敏感证据保留在 ThinkPad 的
`~/rg-lab/cancellation-20261105/dog2-nav-company-entrance-20261105T082532Z/`、Thor 的
`~/rg-lab/navigation-20261105/dog2-nav-company-entrance-20261105T082532Z/`。
