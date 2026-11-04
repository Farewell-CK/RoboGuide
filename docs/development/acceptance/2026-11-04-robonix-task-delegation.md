# 2026-11-04 狗2本机任务委派验收

本次从 ThinkPad 的 RG Controller，通过正式 Node Protocol、狗2 Node 的本机绑定、
同进程 Connector、Robonix Liaison/Pilot 和 Executor 完成了一次原地站立任务。
用户明确授权站立，并确认现场平地、四足周边有空间、无人接触且有人观察。

## 原始身份与结果

| 字段 | 原始值 |
| --- | --- |
| Mission | dog2-delegate-stand-20261104T075534Z |
| Node | lite3-dog2 |
| Operation | task.delegate@v1 |
| 本机 handle | 6c266142-210c-4c4f-a580-9f299ef3d67d |
| Robonix session | 958858f2-a7d6-4dd9-9376-6c9d9c9469ee |
| Robonix 请求 task_id | 5f9c6a97-8f18-4067-90d7-f0ea4060c545 |
| Pilot 内部任务 | T1，归属消息 m1 |
| 原生计划 | 46c8-1、46c8-2 |
| 本机结果 | SUCCEEDED，native_status 为 done |
| RG 结果 | Mission 和 execution attempt 均为 Completed |

计划 46c8-1 只调用 chassis/state，返回姿态 down，电量 81%。计划 46c8-2 调用
一次 chassis/stand，再调用 chassis/state。stand 返回 posture 为 standing，随后
state 的姿态为 standing，数据 age_ms 为 2，电量 80%。本次原生记录无取消请求。
该闭环以 Robonix 报告的本机任务结果为满足依据，现场独立观察结论另行归档。

原始任务、Controller HTTP 观察、attempt 和事件保存在 ThinkPad 的
`~/rg-lab/delegation-20261104/dog2-delegate-stand-20261104T075534Z/`。
原生事件和身份保存在 Thor 的 `~/rg-lab/delegate-20261104/task-journal/pilot.sqlite3`。
原始日志、凭据和运行数据库由实验目录维护。

## 本次发现并修复的问题

1. 首个只读任务在适配器校验时按旧嵌套结构读取 invocation，实际 RG Node 提供扁平
   operation/objective/parameters。请求在本机任务 admission 之前被拒绝。
   修复接收结构并增加真实扁平输入用例。原失败任务使用 Node journal 的正常序列 API，
   在停止服务并核实本机 journal 为零任务后记录有证据的 Failed。原请求和拒绝原因保留。
2. 第二个只读任务完成了 Robonix 状态查询，适配器等待流中不会出现的会话完成事件，
   导致 UNKNOWN。修复为原任务终态、计划根终态和 FinalText 边界联合归约。
   原事件重算得本机 SUCCEEDED，但回传超过 RG 原 deadline，Controller 拒绝任务完成。
   该 Mission 随后通过正常取消入口收尾，历史结果为 Cancelled。
3. 后续只读任务在模型规划阶段收到三次空回复。直接诊断原模型 SSE 接口观察到
   upstream_error，消息为服务器过载。原会话无计划，原生取消回复确认无活动 turn/plan。
   接入层补齐有归属的规划错误加 idle 证据映射，RG 中该任务收尾为 Failed。

这些失败分别保留原 Mission 和原始观察。站立任务使用新的独立授权目标和原始预算，
通过修复后的正常链路完成。现有 Node 二进制与 Controller 二进制继续使用现场基线，
可执行功能通过部署侧任务协议模块与 Node 配置接入。

## 已验证的范围与后续验证

已验证单机注册、能力匹配、资源承诺、Bind、任务派发、原生任务执行、结果回传及
任务完成。协议回归检查在 Windows 和 Thor ARM 运行，包含真实 gRPC/HTTP 和进程中断。
能力目录检查在 WSL Linux 运行。实际 Node 二进制通过新配置的离线 conformance。

本次最终软件检查包括 27 项协议与任务测试、11 项能力目录测试，以及拒绝请求对账
示例的 1 项 Rust 测试。生产协议模块通过 strict mypy，集成目录通过 Ruff 与函数文档
检查，对账示例通过 rustfmt 和 Clippy。固定 proto 文件按原始字节保存，支持跨平台
checkout 后继续验证协议摘要。

真实导航、运动中的取消与停止、跨 Robonix 重启的任务查回、两狗协作和网关设备协作
仍需独立用例。任务 profile 当前是本机委派的声明，严格的逐任务技能权限由 Robonix
后续实现。模型服务过载属于当前实际运行依赖，任务层保留失败原因。

部署复用 `rg-dog2-node.service` 和 `rg-dog2-connector.service`，沿用已启用的自启动配置。
切换前的 unit 保存在新部署目录的 `previous-units`。本次完成后未做整机断电重启验收。
