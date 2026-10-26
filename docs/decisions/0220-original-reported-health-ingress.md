# ADR-0220: Admit actual unchanged health before queued decisions

- Status: Accepted
- Date: 2026-10-26

## Context

真实 HA gate 记录正常 Prepare 的精确拒绝原因为 status_stale。当前 session 的实际
heartbeat 持续到达，而健康事实经过应用队列后已经陈旧。单纯 liveness 不能刷新健康，
不能放松原五秒健康年龄或把旧接收时间改为当前时间来获得候选。

## Decision

Integration 的原 current-session receive evidence 可附带实际 wire-validated heartbeat
健康值。只有该 session 自首个 heartbeat 以来健康分类保持不变才提供此字段；任何健康
变化都关闭该 session 的提前归约，保留完整原 FIFO。Source data 本身不授予 Control 权限。

Control 在原 writer/事务内单独验证 session/lease/当前身份、原 sequence/receive time
与已接纳的连续 liveness。仅接纳与当前已承认分类相同且可调度的实际健康报告，使用真实
原 receive time；wire 未提供远端观察时刻时沿用既有转换规则。重复观察不续期，不从
纯心跳存活推导健康。State payload、任务、Memory、命令和 stop receipt 不走此通道。

正常过期、revocation、健康变化和 stale evidence 仍拒绝；原健康范围已经覆盖的同类
排队 heartbeat 保留其原事件/ACK，但不回退当前状态。不同分类或未覆盖旧事实继续原
拒绝规则。Integration 仍不提前发 ACK，所有接受仍经原 durable Commit/quorum。

Timer 与 Federation 的真实当前决策接纳该输入；Control/Runtime/State/Local Safety 的
权威保持。新增内容为进程内 transport-neutral evidence，持久值与 NodeHeartbeatAccepted
含义不变，不修改 Node、checkpoint 或 event codec，也不建立设备/物理安全事实。

## Validation

确定性回归验证实际原 receive time、无健康值的 liveness 不刷新健康、duplicate/旧 session/
未来时间/身份拒绝、同类旧事实不回退和任意健康变化禁用提前归约。原 transport ACK/事实
窗口、全 Control/Integration/Orchestration/应用模块与实际完整 HA gate 继续必需。
