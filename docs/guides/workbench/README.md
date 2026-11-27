# 对话工作台

代码核对：2026-11-26 · `main@3c7136d6`。

`roboguide chat` 将自然对话连接到已有 Mission Request 与 Controller API。模型提出工具调用，宿主在发送实际操作前核对真实身份、用户意图、已显示计划和原操作日志。

## 本地启动

安装锁定的 Python 环境，初始化明确选择的本地 profile，再启动工作台：

```bash
uv sync --dev --frozen
uv run roboguide init
uv run roboguide chat
```

只读查询、fixture 回放、模型规划和实际设备执行是不同路径。profile 指定实际服务/模型端点与本地凭据；凭据不要提交到仓库，也不能从不可信的当前目录文件隐式改变。

## 一项任务如何推进

1. 说明目标，或明确要求只规划。待补充对话保留完整原始要求。
2. 澄清补齐参数。编号选择仅绑定已显示的问题，不授权执行、审批或取消。
3. 查看完整计划。执行与风险审批绑定确切 revision/digest，并保持独立命令。
4. 等待期间观察原操作。超时或回包丢失保留待对账回执，不创建替代任务恢复它。
5. 区分 Request 取消和已接纳 Mission 的取消。文字回复与 Cancel 回执均不证明设备已经停止。

## 通过诊断处理异常

可询问当前 Node、已登记能力、Mission 进度或一直取消中的原因。宿主读取有来源的 State，并关联确切 Mission/Group/attempt 的停止证据；过期、歧义或不可用保持未知。Runbook 检索提供有界片段及文件行号，文档里的命令只是资料，不是动作授权。

终端在工作时保持输入可编辑，显示有界工具活动，只将完整并确认显示的答复纳入上下文。F3 可查看本轮详情，原始证据与模型摘要分开保留。

## 实现与参考

[任务工具](../../../mission/src/roboguide_workbench/task_tools.py)、[操作日志](../../../mission/src/roboguide_workbench/actions.py)、[Mission Request 引擎](../../../mission/src/mission/request_engine.py)和[任务诊断](../../../mission/src/roboguide_workbench/mission_diagnosis.py)分别承担对应职责。

[版本化工作台 Profile](reference/conversation-workbench-v0.4.md) · [规划流程](../../architecture/v2/system-dataflows.md) · [运维](../../operations/README.md)
