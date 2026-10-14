# ADR-0101：Relation 故障的停止动作与显式安全状态证据

状态：Accepted（软件合同）。日期：2026-10-14。

**实现状态复核：部分接入，RT-G9 尚未关闭。** 下文是已接受的目标合同，不是所有条款
已经进入当前实现的声明。当前 Application 已有 stop purpose、Cancel outbox 和取消终结的
局部 gate，但尚缺 Mission hold、record 自身的 session/export/TTL/策略校验、receipt 阶段、
deadline timer、所有失败/释放/resume 路径的 gate 与专项竞态测试。具体证据和退出条件见
[剩余清单 R01](../development/roboguide-remediation-remaining-2026-10-14.md)。

Runtime 的 relation violation/unknown 继续产生 typed reconciliation evidence。Application 的固定 v0.1 policy 对已有运行 attempt 的 Group 建立 Mission hold，并在原事务中记录 exact attempt/generation/epoch 的停止 purpose 与 Runtime cancellation intent；既有 Cancel outbox 在 Commit 后发送。此动作不授予重复、抢占或替换权限。

CommandReceipt 仅推进 StopCommandAccepted。StopConfirmed 需要两个独立条件：当前 attempt 的真实 Cancelled terminal receive time，以及该 canonical operation 的 LocalSystem 在注册中声明的 State export 所提供的新鲜安全状态。该 export 的 payload schema 必须是 `roboguide.safe-state-observation/v0.1`，值必须精确为 `{"execution_id":"当前 attempt","safe_state":true}`。来源、export、object、semantic、codec 和 TTL 固定为停止时的 descriptor；来源 session 必须仍由 Control 认可，原 received_at 不得早于 stop purpose。多个安全 export 或缺失声明不作猜测。

State 的 DecisionEvidencePolicy 负责明确来源、schema、新鲜度、置信度与冲突；Application 消费结果，State 自身不改变 Control。策略和证据摘要持久化于 Controller wrapper v27。取消 receipt、Unknown、重启、过期、自然 Completed/Failed 或旧 attempt 均不能合成 SafeStateObserved。deadline 只将未确认记录转为 ManualEscalation，保留所有权与原证据；不得自动延长。

新停止记录在 Mission cancel finalization 和 resume 上形成 release/continuation gate。Local EAIOS 仍拥有 hazard、安全状态检测和即时制动的最终权威；该软件合同不是硬实时保证，也不证明传感器或物理安全检测有效。真实设备的 export、stop latency 和 hazard model 需单独部署验收。
