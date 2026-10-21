# ADR-0134：Relation violation 触发 whole-Group stop barrier

状态：Accepted；补齐 Runtime relation violation 到应用停止权威的软件接线。

## 决定

Runtime 对 `RequiresActive`、`SharedSpatialReference` 等当前 relation 产生
`RelationReconciliationRequired` 时，Integration application 通过现有安全停止 authority
对该 Group 的**全部当前非终态 attempt**逐一 durable admit。每个 attempt 保留自己的
generation、Node/session、deadline、Cancel receipt 和独立 safe-state/原 worker exit gate；
任何一个 stop 未确认都继续阻止 release/resume/rebind。

`admit_group_stop` 不再只选第一个 attempt。relation violation 仍不会由 Runtime 直接
改变 Task/Mission lifecycle，也不会把 command ACK、软件进程退出或安全 stop 证明成设备
停止；Control/Orchestration 继续负责后续 recovery decision。
