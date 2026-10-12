# ADR-0084：显式 Future Scheduling Preemption

- 状态：Implemented for explicit uncommitted future planning profile
- 日期：2026-10-12
- 范围：尚未激活、尚未 Commit/Bind 的未来排期；无物理停止

ADR-0082 已提供 logical priority/aging，ADR-0083 已持久化同一 application transaction。
现在允许显式更高 priority 的 Running Mission 请求另一个 Mission 的未来 Task 排期让位。
Orchestration 验证当前优先级、受益 Mission 至少有一个 unbound Ready Task、双方非 held/
cancelling；Control 校验 exact calendar generation、目标 Scheduled/Ready、start 严格晚于
当前时间、没有目标 Task 的 physical assignments/commitments。只释放该 planning interval，
不修改 accepted plan、Task lifecycle、Actor binding、resource ownership 或 retry budget。

HTTP `POST /v1/missions/{beneficiary}/scheduling-preemption` closed body:
`{"victim_mission_id":"low","victim_task_id":"task","expected_calendar_version":9}`。
`GET /v1/scheduling-reservations` 返回当前顶层 `calendar_version`；各 decision 的旧
`snapshot_version` 不是操作 generation。释放后 generation 增加一次，旧请求不可重复生效。
generation/priority/phase 不满足返回 409；body 无效返回 400。事件、完整 checkpoint 和
calendar mutation 在既有 gate/private candidate/SQL transaction 内提交，之后公开结果。
失败回滚全部；新 scheduling 与 Execute 仍走既有 Match -> Propose -> Commit -> Bind。
受影响 Task 的等待年龄保留，下个正常 timer 可重排；没有立即执行或命令发送。

这是显式 planning preemption，默认没有自动扫描/驱逐；当前 due window、Activated、
bound/pending resource-bearing Commit、Unknown/recovery 工作不能被此 API 抢占。
运行中 preemption 仍需要独立 stop/repeat、attempt/generation、ownership handoff 和现场 gate，
不能把 planning interval 删除当作 physical stop。资源不足的高优先级工作仍可 durable defer。

验收：更高 priority/同 generation 成功、stale/equal/held/due/bound 拒绝，后续合法重排、
SQL failure 原 live/calendar/event 回滚，checkpoint restore 保留结果；Core 与真实 HTTP
application path 检查。无模型、设备、Habitat 或 E6。

四项真实 application/Core/HTTP/SQL 回归验证成功/重排/恢复/等待年龄、stale/equal/held/
malformed、due/bound/SQL rollback 和 resource-bearing pending Commit 未 Bind 的边界。
Codec v17 为 typed `SchedulingReservationPreempted` evidence 增加 downgrade fence；
一项 State 反例验证 v16 marker 不得承载该 evidence。Control 还拒绝复用已有 physical
Context resource 的 future decision；这不是借规划排期改变该 Context 的 ownership。
本实现关闭 future planning 子集，运行中的物理抢占继续是独立未实现项。
