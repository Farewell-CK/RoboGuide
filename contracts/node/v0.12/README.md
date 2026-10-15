# Node contract catalog v0.12

Wire identity remains `roboguide.node-protocol/v0.4`. Config v0.10 explicitly negotiates
`roboguide.node.v0.9`; earlier semantic contracts reject its new fields and commands.
MissionPlan v0.9 contains semantic Memory requirements. Control selects the operation-owned
receiver after binding; neither the Mission nor a model selects a Node/provider/path.

`MemoryControl` carries immutable Prepare, once-only Probe or irreversible Revoke. The complete
context and original receiver boot/monotonic budget persist before import. A Prepared receipt
permits only the exact original imports. Completed import history is independent of current
content: each provider declares a fixed read-only availability workflow, proving original
backend instance/configuration plus actual complete manifest/content hashes and byte count.
The closed JSON contracts are in [Memory preparation](../../memory/preparation-v0.1/README.md).

Execute includes exact preparation identity/digest and the accepted Memory requirement array;
even a memory-free v0.9 Execute explicitly carries `[]`. Node rechecks current actual content and
configuration before Local How, and irreversible revocation orders its last dispatch admission.
All pending Memory prerequisites of a Group form a dispatch barrier. CAS reads check current
Controller permission per bounded chunk; the receiver independently checks its original
budget/revocation before every bounded streamed write. Context headers carry no credentials.

Prepare/Probe/Revoke receipts use the existing exact-session management sequence and application
Commit-before-Ack rule. Query receipt ACKs use that same sequence. Duplicate receipts/commands
cannot renew receive time, budget or observation generation. An interrupted probe ID never
performs another backend read. Import ambiguity uses bounded read-only original-provider
queries; it cannot repeat import with a replacement ID.

Node journal v9 accepts existing v7/v8 storage then fences older binaries with both user_version
and its durable marker. Optional preparation support preserves historical provider fingerprints
when absent. Controller wrapper v35 / inner v22 preserve all original evidence and refuse old
binaries, including zero-event revisions. Restore fences outstanding grants before delivery;
no original budget, import side effect or computation is renewed.

The maintained two-Node conformance uses actual exported/imported files, TLS, SQLite failure,
CPU hashing and independent verification. It establishes no robot identity, physical stop,
hardware continuity, hard realtime or overall SoftwareComplete. Active-map, lineage/handoff,
complete GC and the remaining F4–F10 requirements remain mandatory.
