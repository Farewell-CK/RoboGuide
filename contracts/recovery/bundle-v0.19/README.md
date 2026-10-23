# Complete Replica content recovery v0.19

Implementation and actual second-restore/three-leader software acceptance passed under
[ADR-0184](../../../docs/decisions/0184-explicit-replica-content-cold-restore.md).
This explicit profile inherits v0.18's original non-Replica storage interpretations. It accepts
parent8 only with every paired Root4/Root5; v0.18 keeps its original parent3 and codec32 refusal.

`replica-content` has the usual closed component fields, `database: null`, `trees: []`, one
`depends_on` parent component and explicit `storage_schema` equal to
`roboguide.replica-content-layout/v0.4` or `/v0.5`. Each parent declares its actual Controller and
Artifact components when they exist. A member that has never spawned a Controller needs no invented
Controller SQL or Artifact owner. Every declared Controller belongs to exactly one parent. Roots
remain ordinary software storage; no Node, hardware or capability inventory is introduced.

All original supervisor, parent SQL inode, Root writer and Root SQL inode leases precede source
reads. Mandatory tables/heads, same-member original identities, independent operation/pin joins,
active qualified bytes, exact parent current producer heads and original ingress history are checked
before publication. Local Controller projections are validated against their paired committed event
prefix; a replicated authority ID does not become multiple independent authorities. Source owner,
digest and size stay qualified. An unfinished receive may retain its original partial staging and
Unknown without pretending finalized bytes exist. Completed recovery needs its independently
matching actual original Root reader receipt; copying or a normal replacement never fills Unknown.

The pinned bundle adds `replica_content_sources` with each actual original Root/parent backing pair.
Restore v0.19 keeps original operational rows and original identity headers unchanged, and appends
one bounded explicit restore binding. The new binding records the source manifest/SQL digest,
original logical identity, original paired parent, actual destination Root/SQL/parent inode pair and
previous binding digest. SQL layout becomes v0.5. `.restore-binding.json` must match the exact SQL
head and explicitly configured digest. Historical file inodes remain historical observations.

Only explicit process v0.3 with `content.restore_binding_digest` opens restored Root5. The application
binary, policy, cluster profile, membership and original wire contract remain unchanged. This is
not an application binary upgrade. Restore starts no service. A repeat independently verifies all
original rows, source history, current binding and actual backing, then observes only; changed files
refuse rather than recopy. After runtime has modified the restored stores, the old restore command
cannot repair or overwrite them.

`original_authority_bytes_preserved` is false in v0.19 because explicit storage metadata was added;
`original_operational_rows_preserved` is true only after full independent comparison. The separate
binding receipts are storage facts, not execution, current permission, physical stopping, global
lease, cross-host continuity, SLO or SoftwareComplete claims. Online Archive/Snapshot content,
full catchup/joint/upgrade matrices and other non-release stages retain their own acceptance gates.
