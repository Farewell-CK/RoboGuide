# 2026-11-05 狗2站立复验：未通过

用户重新确认现场就绪并授权一次原地站立后，通过 RG Controller、正式 Node、
Connector 和 Robonix Liaison/Pilot 提交任务。本轮没有直接调用底盘动作，也没有重试。
现场先反馈仍趴着，随后补充观察为“站起来了，然后又趴下了”。因此持续站立验收未通过。

## 原始身份与事实

| 字段 | 原始值 |
| --- | --- |
| 部署实现 | `8fd00c4d8de3673de37bedd93af99209235fd275`，沿用同日无动作验收部署 |
| Mission | `dog2-field-stand-20261105T024713Z` |
| Node | `lite3-dog2` |
| 本机 handle | `ecacf799-c744-492d-852a-e8cc5a9bc882` |
| Robonix session | `ac08eb12-c113-4a2c-8701-e8708318a18c` |
| 原始请求 task_id | `707d6c36-9382-4a14-9516-c9f211cd161e` |
| 原生计划 | `1294-3` 查询姿态，`1294-4` 起身一次 |
| 唯一起身 call_id | `1294-4:0` |
| 原生本机记录 | `UNKNOWN`，22 项原事件，最后任务状态仍为 `in_progress` |

任务开始前 RG 为 Reachable/Online、unknown_attempts=0、零占用，原生活动计划为零。
实时底盘查询为 down、电量 79%、age_ms=3、stale=false。

Thor 本机任务于 10:47:13 左右进入，Executor 在 10:47:34.330 调用 stand，
10:47:39.109 收到返回。约 21 秒是进入本机任务至起身调用的时间，约 4.8 秒是
该次 Executor 调用至返回的时间；这些数值不是网络或长期延迟指标。
原计划及 Executor 日志只有一个唯一的起身调用，没有 down、stop 或 move 调用。
日志重复输出同一 call_id，不代表多次执行。

## 已确认的问题与未确认的原因

实际返回是 `success=true`，内容同时为 `status=standing`、`posture=unknown`。
现场 Robonix 源码 `primitives/lite3_chassis/lite3_driver/driver.py:383` 的 stand：

1. 发送起身命令，最多等待 4.5 秒。
2. 等待结束后无论是否确认站立，继续发送 Move、Autonomous 模式命令。
3. 无条件返回 `status=standing`，未站立也仅把 posture 写为 unknown。

起身/趴下协议在该源码中声明为 toggle；本次代码路径只调用一次该 toggle。
稳定窗口的计时也未在中间失去站立时重置，不能证明连续稳定站立。
已核对进程启动路径与 Docker 源码挂载，现场源文件 SHA256 为
`b325b5975694559ac49b8b71e1fe1114bc51b74e6b5ffea19baddc52d34404f6`。

Pilot 最后输出“姿态尚未确认，不再重复起身”，没有追加动作，但原生任务仍为
in_progress，流已结束。因此适配器保留 UNKNOWN，未将自然语言解释视为成功证明。
本次没有完成起身后的第二次原生计划内 state 查询。

规划延迟及上述结果误报均已确认。“起身后又趴下”的直接原因尚未确认：没有保存
当时的底盘 UDP 收包、运动主控状态或同步现场录像，不能据此认定是模型慢、模式切换、
其他控制源或硬件保护中的任何一种。也不能用当前无活动计划证明物理停止。

## 任务收尾与保留的边界

通过 RG 正常 Mission cancel 提交一次取消，返回 202。原生 scoped abort 已回复本会话
无活动 turn/plan，但这不构成设备停止证明。10 秒原停止期限届满后，Controller 保留
ManualEscalation、原资源占用及身份；没有使用无副作用结案，因为本轮已下发运动计划。

10:53 左右复核：Mission 为 Cancelling，unknown_attempts=1，dog2-pilot-task-slot
仍绑定本任务；实时姿态 down、age_ms=1、stale=false、电量 74%，Executor 活动计划为零。
没有清库、释放未知占用、重启服务、修改现场 Robonix 或追加动作。

后续应在 Robonix 修正动作结果、稳定确认及任务终态；RG 继续保留本机执行边界。
需要补齐原运动任务的正式停止/失败证据，才能处理现有占用并开展下一次授权动作验证。

原始任务、认证 HTTP 回执保存在 ThinkPad，原生请求、22 项带原始字节的事件及源码
快照保存在 Thor，各自目录为 `~/rg-lab/cancellation-20261105/` 下本 Mission 同名目录。
非敏感副本位于工作区 `lab/rg-field-cancellation-20261105/` 的同名目录。
前一轮无动作通过结果见[同日取消与恢复验收](2026-11-05-dog2-cancellation-field-validation.md)。
