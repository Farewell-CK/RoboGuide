# ADR-0061: 全集合停止证明后的整组原绑定续跑

- Status: Implemented — 确定性离线、实际 loopback HTTP/gRPC 验证；真实物理续跑未验证
- Date: 2026-10-01

## 问题

ADR-0059 的单 Role 恢复要求 isolated stop；ADR-0060 的 shared-world 本地能力是联合
停止与原世界续跑。取消某个端会影响其余正在执行的端，不能逐 Role 释放或独立重试。
新 attempt 还必须沿用原 endpoint；迁移或重建世界不能保持既有物理效果。

## 决策与权威

新增独立、显式的 Group recovery command，冻结所有当前 attempts、预期 owner 与逐
operation 的重复授权。Runtime 保存有界时间/次数和停止事实；Control 保留资源与绑定
的唯一权威。没有根据 progress 或诊断文本触发的自动取消。

首版支持具有同一 immutable Execution Session 的 independent 执行，任意有界成员数。
需原始 dispatch 与当前注册都声明 exact operation 的 `execution-group / repeat-after-stop`。
旧声明、旧 attempts、缺少 session、执行期 relation 或未提供的协作机制 fail closed。
该实现不猜测任务需要几个机器人，也不改变 MI 或 MissionPlan 语义。

请求必须精确覆盖全部 current attempts，逐个提供原 attempt ID、expected owner 和重复
授权。已 Completed 成员保持原事实、不 Cancel、不重跑；其余成员必须明确授权。
Failed 或已有非 recovery Cancelled 不构成可恢复的原上下文。请求 ID、成员与预算不能
在重试时改变；每 Group 最多 16 个授权轮次，每轮最多 32 个成员。

只有所有受影响原 attempts 分别报告真实 Cancelled（或自然 Completed）、实际接收时间
处于原授权期限内，才进入 continuation。Unknown、接收回执、心跳或取消指令都不是
停止证明。缺失任一事实不释放资源、不准备替代；超时保持可观察的 fenced 状态。

首版是 same-owner continuation：原资源承诺在暂停期间保留。Control 经 Proposal ->
Commit 重新检查当前节点、exact capability/operation、注册声明、Actor 物理身份和全部
资源归属/容量，确认沿用原绑定；不新增预约、不选替代节点、不释放 Completed peers。
Runtime 仅为 Cancelled slots 一次性准备完整的新 attempts 与 outbox，在 checkpoint
持久化之后才发送。任何准备失败都回滚整个应用事务，不发布半组替代。

每次 outbox delivery 再检查原期限、完整集合和当前 support。续跑期间不能偷偷新增
Ready Task 或进行单 Role recovery。普通 Cancel/Mission Cancel 覆盖恢复授权；停止事实
与旧 attempt 历史保留。重启不续预算，未获得真实终态的物理 attempt 仍为 Unknown；
不借恢复创建新 Habitat reset 或新的 Mission Request。

应用 timer 和 Node fact 接纳沿用同一 receive clock；事务完成后重新读取当前时钟，
每条 outbox command 独立读取时间，不使用持久化或前一条发送之前的时间判断 Execute
是否仍在预算内。Runtime 保存准备时间及新 attempts 首次实际接纳的 receive time；
晚于期限的真实事实保留，但不能把 BudgetExpired 改成 Continued。及时接纳完成后，
该预算不限制原 Mission 的正常后续物理执行时间。已完成的新 peer 可按原 Mission
satisfaction 契约释放自身资源，不要求它重新执行或重新获取资源来续跑剩余 slots。

完整准备与持久化是应用事务；网络到达不是原子事务。部分已收到、其余未收到或
迟到时仍保留原承诺、显示 fenced 状态，等待真实事实与显式处置，不伪造全组成功。

## 协议与证据

`POST /v1/groups/{group_id}/recover` 使用独立 versioned command；只读
`GET /v1/groups/{group_id}/recovery` 显示原集合、期限、停止确认、准备的替代与阶段。
它不是 MI endpoint，也不允许指定替代 Node、参数或动作。

命令为 closed `roboguide.group-recovery-command/v0.1`，字段：`recovery_id`、
`members[{execution_id, expected_node_id, repeat_authorized}]`、`timeout_ms`（1–3,600,000）、
`max_replacements`（1–16，整个 Group 授权轮次上限）。必须列出每个 current attempt；
已 Completed peer 可不授权重跑。`expected_node_id` 只校验原 owner，不选择新 owner。
202 仅表示授权已持久化，不能视为已停止、已续跑或已成功。未知/不支持/集合不一致的
运行条件返回 409；非法请求返回 400。重复同一 ID 不续期限、不改变预算。

Controller checkpoint v18 保存 Group recovery history；兼容 v17/v16/v15/v14 默认无 Group
恢复授权，拒绝不一致成员、伪造停止时间和跨集合 replacement。Control continuation
事件保留逻辑 slots 与 unchanged assignments；官方 benchmark 判定和 Formal admission
保持原协议。

## 离线验收与能力边界

部署入口保持默认关闭：`ROBOGUIDE_B1_RETAIN_STOPPED_SESSION=1` 同时启用 Local Adapter
保留世界与运行目录 Node 声明。启动工具只派生已有 operation owner 的恢复 metadata，
不改变资源、能力、场景或 Mission。完整 Node config 经生产 Node binary 校验后冻结
原始字节摘要；Controller/Node/MI 启动前核对两个固定只读 support route，配置变化、
HTTP 错误或超过 64 KiB 的响应 fail closed。默认模板仍声明 unsupported。

零 Provider/Simulator 的 `tools/quality/check_group_continuation_processes.py` 启动真实
Controller 和两个真实 Node daemon，使用明确标记的 synthetic Local EAIOS fixture。
Cancel 回执不产生停止事实；部分真实 Node Cancelled 仍不续跑；同 owner/session/intent
续跑只创建必要的 attempts，Completed peers 不再 dispatch。检查保留 run-local journals、
Control checkpoint、日志和二进制摘要，不覆盖历史文件，也不声称 synthetic completion 是
物理世界验证。真实 Habitat continuation 需独立受控运行证明。

验证多成员/多 Task 原绑定、Completed peers、部分/错误/迟到停止事实、预算/idempotency、
注册与资源变化、持久化失败、restart、Mission cancel、完整新 outbox 和实际 HTTP 路由。
单 Role coupled-stop 拒绝测试继续通过。Local adapter 的世界/预算 fencing 保持独立。

本轮不运行真实 Provider/Habitat；确定性 Node doubles 不能证明真实物理 continuation。
新的执行计划、迁移、自动 stall recovery、真实紧密协作及 world 重建不在本决策范围。
