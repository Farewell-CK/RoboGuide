# 现场到达验收

在任务里选一次目标。实验脚本从 Controller 已接纳的导航任务中提取目的地，观察者确认屏幕上这台机器人到了该地点并停稳。同一租户、同一观察源可以复用到不同 Mission。

从仓库根目录运行。以下示例使用已有 Mission Python 环境。

```bash
export PYTHONPATH=mission/src:.
python tools/onsite_arrival/arrival_goal.py --peer admin.json enroll --observer field-observer
```

管理员只需登记一次。重复执行相同登记会返回已有来源。peer 文件使用部署已有的证书，路径相对于该文件。示例中的名称应替换为实际授权主体和文件。

```json
{"endpoint":"https://localhost:8443","tenant":"local","principal":"installer","certificate":"installer.crt","key":"installer.key","ca":"ca.crt"}
```

每个 Mission 接纳后、执行结束前，在实验脚本原先安装 revision policy 的位置调用一次：

```bash
python tools/onsite_arrival/arrival_goal.py --peer admin.json install "$MISSION_ID" --policy base-policy.json
```

`base-policy.json` 是实验原有的 policy 对象，保留 `policy_id`、`proposer`、预算、切换限制和 triggers，去掉原先手填的 `goal_verifier` 和 `goal_context`。已有安装逻辑也可以直接复用下面的 Python 调用，`revision_view` 来自该 Mission 的 `GET /v1/missions/{id}/plan-revisions`：

```python
from tools.onsite_arrival.arrival_goal import policy_fields

policy.update(policy_fields(revision_view["current_plan"]))
```

默认验收原任务指定的物理实体。原始 Mission 允许经审核的接替时，安装时传 `--allow-handoff`，或在 Python 里传 `allow_handoff=True`。验收会沿原 Context 和 ContextRole 找到当前任务，显示当前 Actor 和执行 Node。多个可能的导航任务需要用 `--task` 指定要验收的那个，地点仍从任务读取。若一次执行历史中同一组、同一角色出现多个尝试且无法明确选定，工具会报告歧义。

对于 `mobility.navigate@v1`，冻结任务的 destination 及同一组地图、坐标等参数。对于 `task.delegate@v1` 的 `local-task-v1`，同一操作也可用于拍照或读取电量。安装时用 `--task`（Python 用 `task_id`）指定到达任务，工具从该任务冻结并展示整段原文。接替后若重写了这段话，即使地名看起来相同，也会要求核对并拒绝提交。

现场观察者使用自己的 peer 文件。该主体需要本租户现有的 Mission goal 发布授权，并且独立于安装者和提案者。命令显示机器人和目标，输入 `yes` 会重新核对当前任务并提交一次，其他输入结束本次观察。

```bash
python tools/onsite_arrival/arrival_goal.py --peer observer.json confirm "$MISSION_ID"
```

输入 `yes` 后，工具开始计时，从五秒预算中扣除复核任务的耗时，再把剩余时间分给发送请求和 Controller 收到后的有效期。来源要求更短时采用其上限。Mission 进入隔离或观察过期时须重新观察。

成功时返回 Controller 的原始回执。如果回复丢失，工具会用本次观察的摘要查一次 Controller 历史；查到会显示 `recorded` 及处理状态，查不到会打印查询路径和摘要。Mission 最终是否完成可从 Controller 的 Mission 状态查看。
