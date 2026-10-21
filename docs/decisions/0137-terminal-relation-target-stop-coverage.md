# ADR-0137：关系异常保留终态目标的停止门禁

状态：Accepted；修正 ADR-0134 的入口选择，现有 stop authority/schema 保持。

## 决定

应用 `safety_stop::admit` 已覆盖 Group 全部当前非终态 attempts，并额外覆盖 relation 的
exact target。目标收到 Completed 但未曾获得有效 relation proof 时，Runtime 仍可能产生
Unknown/reconciliation；不能因为本地 Completed 就删除其独立停止与 Mission hold 门禁。

relation 入口必须把原 Task/Role target 传入 `admit`，不能改为从 Group 找一个非终态
attempt。后者在其他 attempt 活跃时漏掉终态目标，全部终态时完全不建立 hold。普通
Group cancel 仍只需选一个当前活跃 attempt，由同一个 `admit` 完整覆盖 Group；重复逐个
调用不会增加 authority，反而重复扫描。

原 generation、Node/session、deadline、receipt、原 worker exit/safe-state 以及 ownership
规则保持。终态目标缺少独立声明/证据时进入原 ManualEscalation，不编造 Cancel receipt，
不把本地完成升级为停止或设备安全。修正不引入新持久字段或兼容版本。
