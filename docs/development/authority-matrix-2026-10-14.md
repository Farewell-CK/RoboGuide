# RoboGuide Authority Matrix v0.1

本矩阵是 2026-10-14 整改的基线。投影、观察和证据不得反向授予资源 ownership、Lease、Group lifecycle 或物理安全权限。

| 领域 | 唯一权威 | 可写证据/投影 | 明确无权 |
| --- | --- | --- | --- |
| Mission Request | Mission Service / Mission Intelligence | request、dialogue、review、approval、principal audit | Node 选择、Resource Commit、设备控制 |
| MissionPlan | Mission Intelligence（语义准入）+ Orchestration（accepted plan） | plan digest、grounding、review、repair | live inventory、ActorBinding、Local How |
| Match/Proposal | Control Match/Scheduler boundary | candidate、proposal、calendar evidence | 物理命令、Task satisfaction |
| Reservation/Lease | Control | reservation、lease、commit/release evidence | State projection 反向修改 ownership |
| Group/Binding | Control | Group、Role、ActorBinding、Recovery Commitment | Local EAIOS 高频控制 |
| Task/Mission lifecycle | Orchestration | Ready、Running、AwaitingSatisfaction、Satisfied、Rejected、Failed、Completed | 直接占用物理资源 |
| Execution attempt | Runtime | logical slot、attempt、generation、command intent、fact、Unknown | replacement selection、Commit、Task satisfaction |
| State evidence | Source/provider + State projection | source-aware record、TTL、received_at、confidence、Belief | Global Truth、隐式冲突裁决 |
| Decision evidence | DecisionEvidencePolicy + consuming authority | policy digest、reducer result、stale/unknown reason | 未授权 State 改变 Control |
| Memory manifest/catalog | Memory provider + Artifact/State catalog | manifest、revision、replica、import ACK、provenance | 自动复制、Mission lifecycle |
| Artifact bytes | Artifact Store/provider | CAS blob、digest、size、retention、ACL audit | Control/Runtime authority |
| Node protocol/session | Integration + Node Service | registration、sequence、journal receipt、route/liveness | Task/Mission terminal decision |
| Local execution | Local EAIOS | local outcome、safe-state、stop confirmation | 远程覆盖 Local Safety |
| Physical safety | Local EAIOS safety + RoboGuide evidence gate | StopRequested、SafeStateObserved、StopConfirmed | 未确认时释放/重放/交接 |
| Controller HA | quorum/leader authority | term、epoch、membership、checkpoint/archive evidence | Artifact bytes、物理连续性推断 |
| Observability | diagnostics/metrics pipeline | metrics、logs、traces、alerts | 改变事实、ACK、调度或 ownership |

## 关键不可变规则

1. Proposal 不产生 reservation；Commit 是唯一资源承诺入口。
2. State/Allocation projection 可以滞后，但不能授予或撤销 ownership。
3. Node journal receipt 证明 durable admission，不证明执行结果或物理停止。
4. `CancelAccepted` 不等于 `StopConfirmed`。
5. 没有 `StopConfirmed`，不得释放物理 ownership、自动重放或执行 Actor takeover。
6. Memory discovery 不授权复制；Import 必须有显式 command、provider authorization 和 durable ACK。
7. Replica history 不证明 PhysicalContinuity。
8. Metrics、diagnostics 和 health endpoint 不得改变事实、ACK、调度或 authority。
