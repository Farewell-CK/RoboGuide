# 2026-11-05 模型恢复后的狗2站立验证

用户确认模型凭据已恢复并允许继续原地站立验证。先通过 RG 完成只读任务，随后通过
RG Controller → 正式 Node → 同进程 Connector → Robonix Liaison/Pilot → Executor
发起一次站立。现场反馈为“完全没有起身”，本次持续站立验证未通过。

后续用户表示已找到原因并另行授权一次站立复测，新任务原生 standing、T1 done，RG
正常 Completed。详情见[后续成功记录](2026-11-05-dog2-stand-recheck-success.md)，
本篇保留前次未起身的原始事实。

## 实际结果

| 项目 | 结果 |
| --- | --- |
| 只读 Mission | `dog2-field-read-20261105T064516Z`，Completed |
| 站立 Mission | `dog2-field-stand-restored-20261105T064555Z`，Failed |
| 站立本机 handle | `3c7acf23-980b-4ee8-9ac0-a5fbc09f37db` |
| 站立原生 session | `14e193a7-aacc-4ae3-903d-184a0eddfd91` |
| 站立原始 request task_id | `3a770336-79b6-4e6a-9ee1-83b2cb0bc2b0` |
| 原生计划 | `18a6-3`、`18a6-4` |
| 原生 Task T1 | failed，m1 及全部原计划归属一致 |
| 原事件 | 只读 13 项、站立 24 项，完整字节与归属均保存 |
| 取消/abort | 两项任务均为 0，不需要人工结案 |
| 最终 RG | ready=true、degraded=false、unknown_attempts=0，资源占用为零 |

只读任务实际调用一次 chassis/state，报告 down、电量 52%，证明恢复后的 Pilot 模型
调用可以正常完成，不再以“入口在线”推断模型凭据可用。

站立任务只有三个唯一调用，均来自原计划，参数均为 `{}`：

1. `18a6-3:0`，chassis/state：down，数据新鲜，电量 52%。
2. `18a6-4:0`，chassis/stand：RPC/工具调用返回 success=true，但内容为
   `status=standing`、`posture=unknown`。
3. `18a6-4:1`，chassis/state：down，age_ms=3、stale=false、电量 52%。

Executor 记录起身调用时间为 14:46:13.249，随后状态调用为 14:46:18.015。
没有 down、stop、move 或导航调用，没有重复起身。日志对同一 call_id 的重复输出不
代表第二次执行。任务没有因为计划根成功就判为成功；Pilot 在归属明确的 TaskState
将 T1 置 failed，并在原 FinalText 报告未达到站立确认标准。

本机适配器按原任务结果归约为 FAILED，RG attempt 和 Mission 正常进入 Failed，
资源释放。相比上午留下 in_progress 的情形，本轮明确终态使任务正常收尾。
这个差异是本轮实测事实，不宣称已修改或普遍修复了 Robonix 的任务终态实现。

## 只读诊断与边界

现场用户确认“完全没有起身”，与最后的 down 姿态相符。收尾独立查询为 down、
电量 50%、age_ms=4、stale=false；Executor 活动计划 count=0。

本轮没有修改 Controller、Node、Connector 或原生 Robonix。沿用 Controller/Node
`8fd00c4d` 现场二进制和 Connector 的 `238abcb4` 人工结案模块；本次没有调用人工结案。
原生底盘源码 SHA256 仍为
`b325b5975694559ac49b8b71e1fe1114bc51b74e6b5ffea19baddc52d34404f6`。
boot ID 仍为 `e4e61ef5-3ff4-467e-bbe8-33db3f382c83`，凭据恢复后 Liaison 新 registration
为 `e85f597d-7a38-4ced-892c-09276495e916`。

底盘驱动源码默认命令目标为 `192.168.1.120:43893`，配置允许覆盖目标。
本机路由到该 IP 为 enP2p1s0、源地址 192.168.1.130；ARP 可解析，单次 ping 成功，
容器 network mode 为 host。这只验证该默认主控地址的基本连通性，未证明实际运行
配置没有覆盖目标、UDP 指令到达、主控受理或控制权可用。

已确认驱动在没有确认站立后仍返回 standing，发送异常也会被吞掉。驱动真实发送、
主控接收/拒绝、控制模式及保护状态缺少本次原始证据，不能据此选定未起身的直接原因。
没有追加动作来探测，也没有把命令回执当作物理停止证明。

原始任务、认证 HTTP 回执位于 ThinkPad `~/rg-lab/cancellation-20261105/` 的两项
Mission 目录。完整原事件、驱动源码及选定原生日志位于 Thor
`~/rg-lab/stand-restored-20261105/`，非敏感副本保存于工作区
`lab/rg-stand-restored-20261105/`。密钥和运行数据库没有导出。
上一轮凭据错误与管理员结案见[此前重试](2026-11-05-dog2-stand-retry.md)。
