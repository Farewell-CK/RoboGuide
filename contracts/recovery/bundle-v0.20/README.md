# Complete Replica content recovery v0.20

Recovery profile v0.20 adds explicit Root6 Snapshot receive prerequisites while retaining the
v0.19 Root4/Root5 interpretation unchanged. A `replica-content` component may declare
`roboguide.replica-content-layout/v0.6`; the component must also declare a nullable
`restore_binding_digest`, and the actual `backing_mode` (`original` or `restored`) must match that
declaration. Root6 inventories include the mandatory `snapshot_receive_ids` head,
`content_snapshot_receives` records and `SnapshotReceive` references.

Each receive record retains the complete original authenticated source/member/Root/TLS leaf,
SnapshotMeta, payload digest, voting target, qualified inputs and nonrenewing process budget.
Incomplete receive remains Unknown and keeps its pin. A byte-ready record is valid only when every
same-Root CAS receipt and actual file identity is independently verified. Parent Snapshot
publication retires those pins through a separate proof; byte readiness never means Snapshot
application.

The v0.20 restore binding uses schema `roboguide.replica-content-restore-binding/v0.2` and records
source Root6, exact paired parent backing and the previous binding digest. Restore starts no service,
does not renew a budget and does not establish a Node, device, hardware or physical-continuity fact.
Older v0.19 recovery entry points reject Root6 content authority.
