# ADR-0134：Relation violation 触发 whole-Group stop barrier

状态：由 [ADR-0137](0137-terminal-relation-target-stop-coverage.md)修正。原 `admit` 已经
覆盖全组 peers，不能通过重新选活跃入口漏掉 exact terminal target。

## 决定

Runtime 对 `RequiresActive`、`SharedSpatialReference` 等当前 relation 产生
`RelationReconciliationRequired` 时，Integration application 通过现有安全停止 authority
保留原 Task/Role target 传入已有全组 `admit`，覆盖当前非终态 peers 与 exact target。
即使 target 本地 Completed，缺少 relation proof 仍须保留停止门禁。每个 attempt 保留自己的
generation、Node/session、deadline、Cancel receipt 和独立 safe-state/原 worker exit gate；
任何一个 stop 未确认都继续阻止 release/resume/rebind。

普通 `admit_group_stop` 选一个活跃入口即可复用原全组覆盖；旧记录曾把这个入口选择
误述为单 attempt 覆盖，这一判断已修正。relation violation 仍不会由 Runtime 直接
改变 Task/Mission lifecycle，也不会把 command ACK、软件进程退出或安全 stop 证明成设备
停止；Control/Orchestration 继续负责后续 recovery decision。
