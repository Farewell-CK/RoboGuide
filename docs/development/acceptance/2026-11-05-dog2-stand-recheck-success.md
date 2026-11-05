# 2026-11-05 狗2站立复测：RG 正常完成

用户明确要求“你再试一次，找到原因了”。沿用已确认的现场观察与原地站立范围，
经 RG 正常 Mission、Node 和 Robonix Liaison/Pilot 下发一个新的独立任务。
本机执行结果与后续实时状态均确认 standing，RG Mission 正常 Completed、资源归零。

## 原始身份

| 字段 | 原始值 |
| --- | --- |
| Mission | `dog2-field-stand-recheck-20261105T070706Z` |
| execution | `attempt-47:group-dog2-field-stand-recheck-20261105T070706Z-13:delegate-task-13:delegate-role-1` |
| 本机 handle | `ad9a0b3a-2573-4bd5-b5f7-17e9e91e077d` |
| 原生 session | `9fb239a2-bb4c-47a0-b3f4-40f53a066267` |
| 原始 request task_id | `7a252850-e480-45a6-9739-81b9870113d4` |
| 原生计划 | `18a6-6`、`18a6-7` |
| 原生 Task | T1，归属 m1，最终 done |
| 原生完整事件 | 25 项，本机终态 SUCCEEDED，序列 29 |
| RG Mission / attempt | Completed / Completed |

开始前原生无活动计划、适配器无未决 run，RG 在线、零占用、unknown_attempts=0。
底盘实时姿态 down，电量 36%、age_ms=3、stale=false。

## 唯一动作及确认

三个原生调用均由 Robonix 原计划产生，参数均为 `{}`：

1. `18a6-6:0` chassis/state：down，电量 35%、age_ms=2、stale=false。
2. `18a6-7:0` chassis/stand：status=standing、posture=standing。
3. `18a6-7:1` chassis/state：standing，电量 35%、age_ms=1、stale=false。

Executor 日志记录起身调用于 15:07:24.647，下一个状态调用于 15:07:27.951。
同 call_id 的日志重复输出没有形成第二个计划调用。没有 down、stop、move 或导航调用。
15:08:20 左右 Pilot 最终给出 T1 done 和原 FinalText，随后适配器、Node 与 RG 正常
回传成功。没有取消、abort、人工结案或任务重发。

15:09 左右的独立只读复核仍返回 standing，age_ms=0、stale=false、电量 32%。
原生活动计划 count=0，RG 无资源占用，ready=true、degraded=false、unknown_attempts=0。
站立任务的正常完成不产生设备停止、运动取消或自动执行下一任务的权限。

## 版本与本轮观察边界

本任务没有修改代码、配置或重启服务；用户的现场处理细节待补充。
Controller/Node 沿用 `8fd00c4d` 现场版本，
Connector 保留 `238abcb4` 的人工结案模块，本轮未调用该模块结案。
原生底盘源码 SHA256 仍为
`b325b5975694559ac49b8b71e1fe1114bc51b74e6b5ffea19baddc52d34404f6`。
boot ID 为 `e4e61ef5-3ff4-467e-bbe8-33db3f382c83`，Liaison registration 为
`e85f597d-7a38-4ced-892c-09276495e916`。

用户表示已找到此前原因，但具体原因及处理方式尚未提供；本轮没有据此推定控制模式、
遥控器或硬件保护中的任何一种原因。已请求现场独立反馈是否站稳，当前文档以原生
执行结果和实时状态为依据，尚未取得本次现场反馈。

15:08:07 左右一次 `/readyz` 返回 503，正文为 authority storage or quorum unavailable。
随后 15:08:42、15:08:57 的查询均为 200，Controller 主 PID 1414757 未变，磁盘尚有
约 25 GB 可用；没有重启或修改存储。原错误回执已保存，不能因后续成功删去。
本次未捕获 readiness 具体内部错误，不能断定是 SQL、在途批次或其他原因。

原生完整字节、身份、源码及选定日志位于 Thor `~/rg-lab/stand-recheck-20261105/`。
认证 RG HTTP 观察位于 ThinkPad 原 Mission 同名目录，非敏感副本位于工作区
`lab/rg-stand-recheck-20261105/`。未导出密钥、环境文件和运行数据库。
此前未起身的失败仍保留，见[前次验收](2026-11-05-dog2-model-restored-stand.md)。
