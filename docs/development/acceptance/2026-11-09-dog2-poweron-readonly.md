# 2026-11-09 狗2上电后的 RG → Robonix 只读任务验收

## 结论

用户报告两台底盘上电后，本轮继续只读检查。随后用户明确要求狗1不由本任务负责，
已停止狗1排查；此后仅向狗2下发一项只读任务，未修改或重启任何机器人服务。

狗2正式 Mission `dog2-poweron-read-20261109T014552Z` 为 Completed。
同一 attempt 对应的 Robonix 原生任务为 done，只有一个计划、一次只读 chassis/state 调用。
原始查询返回 posture=down、battery.percent=97.0、present=true、stale=false、age_ms=1。
没有站立、趴下、移动、导航、速度或其他执行器调用。本轮未进行现场姿态目视确认，
这里的姿态是 Robonix 原始状态查询报告，而非额外的物理效果验收。

任务完成后 Controller ready=true、degraded=false、unknown_attempts=0，allocations=[]；
狗2 Pilot 入口重新 READY，Node 和统一 Connector 均 active。没有留下待处理任务。

## 实际入口与完整关联

1. 通过 ThinkPad 原管理员 mTLS 管理入口读取 Controller `/readyz`、inventory 和资源占用。
   只有 `lite3-dog2` 当时声明可用的 `task.delegate@v1`，任务前没有 Unknown 或资源占用。
2. 使用仓库已有 `scenarios/local-task-delegation/mission-plan.json` 的结构化 MissionPlan v0.7，
   替换唯一 Mission ID 及只读目标，通过 `POST /v1/missions` 提交。
3. 一个 Context、一个 Task、一个 Role，coupling_mode=independent，使用
   local-task-v1 profile 和 dog2-pilot-task-slot。没有双狗协作或 SequentialHandoff。
4. RG 通过原 Mission/Matching/Control/Commit/Bind/Node 流程派发，Node 委派完整只读目标
   到统一 Connector 的既有 Robonix 任务适配器，再通过 Liaison 交给 Pilot。
5. Pilot 自行生成一次只读计划，Executor 调用 chassis/state，收到成功 leaf result；
   原 Task 状态及 FinalText 到达后，适配器归约 SUCCEEDED，RG 收到 Completed 并释放资源。

| 标识 | 原始值 |
| --- | --- |
| RG Mission | dog2-poweron-read-20261109T014552Z |
| RG execution | attempt-40:group-dog2-poweron-read-20261109T014552Z-13:delegate-task-13:delegate-role-1 |
| Node | lite3-dog2 |
| Native session | 6ddcc10b-641f-4765-b8c1-79d0bcc36a96 |
| Native request task / turn | c6949963-b6f5-423e-8297-f123515302eb |
| Pilot 内部 Task | T1，归属原请求 m1 |
| Native plan | 9890-1 |
| Native call | 9890-1:0，robonix/primitive/chassis/state，provider=lite3_chassis |

本次由 RG 下发的是“只查询并报告当前姿态和电量”的目标。具体只读能力由 Robonix
Pilot 选择；没有在助手侧直接调用该能力来替代 RG Task。SSH 只用于部署诊断和核对
原始日志/持久回执，不承担这次任务的下发入口。

## 状态与部署边界

狗2开机 boot ID 为 `9a58bed5-89cc-4c7a-8938-7e15391dd899`，与此前主机验收不同；
RG Node、Connector 已自行启动。Controller host-status State 未过期，独立传感器观察
中电池、里程计、RGB 有新数据，Mapping 报告已加载 1101_visual_dog2 并处于 localization。
这是服务报告状态，不独立证明定位质量、共享地图坐标系或导航目标可达。

本轮 MI 未参与。虽然查询了 assess-initial-support，该入口返回 source_unconfigured，
scope=initial_static_world；这次部署没有配置该静态 World assessment 来源，不能把它
当作空间预检通过。正常 Mission 的运行时匹配、资源分配和原只读任务执行已实际完成。
observer 原 navigation_readiness 仍为 UNAVAILABLE，不因 Pilot 任务入口 READY 而提升。

本机 Pilot journal 从 28 runs / 492 events 增加到 29 runs / 505 events，新增正好一个
任务和 13 个原生事件。原先 28/492 行按 rowid 的完整摘要前后不变：

```text
runs:   fdde9d1f854904a453819964bc317d3ddf7153d02807b18f4f489189b27e76ee
events: 0b95be0f924d461d3afc45bb28d0c727e453b8502ff07045955400db1cfc894e
```

本轮未改 RG 或 Robonix 功能代码，也未触发服务重启。没有新增离线软件测试来替代
这次真实验收。公开证据位于 `C:/workspace/DEAIOS/lab/rg-two-chassis-poweron-20261109`：
dog2-submitted-plan.json、dog2-read-rg-result.json、dog2-read-native.json、
dog2-final-controller.json、dog2-final-local.json、dog2-acceptance-summary.json 及 SHA256SUMS。
ThinkPad 对应目录为 `~/rg-lab/dog2-poweron-preflight-20261109`。

运动授权保持独立。此结果只验证上电后的单狗只读委派链路，不代表完成 MI 规划、
运动验证、双狗协同、取消中的物理停止、断链恢复或自动接管。
