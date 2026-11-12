# 2026-11-05 狗2站立重试：模型凭据拒绝

用户再次要求继续尝试站立。本轮只通过正式 RG Mission 委派 Robonix，没有直接调用
底盘动作。此前的现场站立结果见[上午记录](2026-11-05-dog2-stand-field-validation.md)。

后续：用户恢复模型凭据后，14:45 的 RG 只读任务完成；新站立任务仍未起身，但已正常
报告 Failed 并释放资源。当前结果见[模型恢复后验证](2026-11-05-dog2-model-restored-stand.md)。
下文 API_KEY_DISABLED 和恢复条件描述的是本轮原始中断时刻。

## 旧任务结案与部署

狗2本次 boot ID 为 `e4e61ef5-3ff4-467e-bbe8-33db3f382c83`，此前为
`a819edf1-195f-43fe-b8dd-8534821a3b6b`。新的 Liaison registration 为
`a8111812-ae6f-493e-9ce7-880c27f8f176`。重启没有清除原 UNKNOWN，任务槽仍保留。
独立只读核验取得零原生活动计划和三项新鲜的 down 姿态样本，电量约 78%。

冻结源码 `238abcb46b466881184242ffc890d94667d2f0e8` 的本机人工结案工具经过
[软件验收](2026-11-05-local-cancel-settlement.md)后，仅更新原 Connector 中的
`robonix_task/journal.py`、`manual_settlement.py`。Controller、Node 二进制及 Robonix
Pilot、底盘驱动均未修改。底盘源码 SHA256 仍为
`b325b5975694559ac49b8b71e1fe1114bc51b74e6b5ffea19baddc52d34404f6`。

上午 Mission `dog2-field-stand-20261105T024713Z` 先经过离线本机管理员结案：

- 实际主体为 `local-os:localhost.localdomain:nvidia:1000`，命令为
  `field-local-cancel-stand-20261105-v1`，原序列 30 变为 31。
- 原状态 UNKNOWN、原详情与完整行/事件摘要保存在新回执，原事件逐项完全相同；
  其他 runs 完全相同，原请求、会话、task_id、创建时间和取消身份不变。
- Node 查回原 handle 后，RG execution 收到 Cancelled。
- Controller 经实际 mTLS recovery-admin `field-operator:33457` 接受命令
  `field-controller-stand-cancel-20261105-v1`，原 Mission 变为 Cancelled、占用归零。
  原停止记录和原期限逐项相同。

两级决定均明确不产生 physical StopConfirmed 或自动续跑权限，本机决定不导出
no-effects。本次新任务的权限来自用户新的重试指令。

## 本次唯一新任务

| 字段 | 原始值 |
| --- | --- |
| Mission | `dog2-field-stand-retry-20261105T061447Z` |
| 本机 handle | `d8a4518f-fed1-4fe6-b8ee-2959113bc0eb` |
| Robonix session | `b6ed43db-ca73-44e7-83b5-aec6d5de966f` |
| 原始 request task_id | `e5617b59-bf40-4944-b18e-15a3251dff39` |
| 实际原生计划 | 0 |
| 实际起身/趴下/导航调用 | 0 |
| 最终 RG Mission | Cancelled，经独立管理员结案 |

14:14:47 的正常 RG 发布返回 202，原生 Pilot 接收了新会话及目标，随后模型开流失败：

```text
HTTP 401 Unauthorized: {"code":"API_KEY_DISABLED","message":"API key is disabled"}
```

Pilot 自身重试一次模型开流后返回同一错误；这不是 RG 重发任务。完整 4 项原始事件
只有 turn、任务 in_progress、规划状态和 scoped idle-abort 回执，没有计划或能力调用。
本轮未完成站立。这个错误发生在 Robonix 的模型凭据环节，不能反推上午站起又趴下
也是同一原因。上午动作现象的直接原因仍未确认。

## 收尾与剩余条件

本轮 Mission 通过正常 RG cancel，原生没有活动 turn/plan。依据同一完整事件、原模型
401 错误和新鲜 down 姿态，在 Connector 停止期间通过命令
`field-local-model401-cancel-20261105-v1` 记录本机人工取消。Controller 随后通过命令
`field-controller-model401-cancel-20261105-v1` 独立结案。两者仍为 stopped 的管理员判断，
没有伪造原生 interrupted/failed，也没有导出 no-effects 或设备停止证明。

最终 RG 为 ready=true、degraded=false、unknown_attempts=0，资源占用为零。
原生 journal 为 12 条 runs、137 项原事件、0 个未决 run、2 项本机管理员回执，profile v2。
最后只读姿态为 down、电量 73%、age_ms=2、stale=false，Executor 活动计划为零。
新代码恢复 v2 已在本机再次启动中校验通过；没有清空数据库或重放旧任务。

恢复有效 Robonix 模型密钥是下一次任务的前置条件。密钥不进入聊天、报告或仓库。
本轮没有修改、替换任何模型凭据；凭据恢复后仍需按原授权范围核验当前状态再执行。

非敏感原始证据保存于工作区 `lab/rg-stand-retry-20261105/`、Thor 同名实验目录及
ThinkPad `~/rg-lab/cancellation-20261105/` 的原 Mission 目录。导出仅包含 JSON、
选定错误日志和软件测试结果，不包含运行数据库、私钥或环境文件。
