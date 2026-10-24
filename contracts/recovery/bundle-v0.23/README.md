# Recovery bundle v0.23

This explicit interpretation requires complete parent9/Root8 pairs. It inherits v0.22's original
storage, writer leases, operational history, exact qualified CAS bytes, restore bindings and empty
private-staging rule. Earlier profiles refuse parent9; accepted old bundles keep their meanings.

Each parent retains mandatory `content_initialization_policy` and `content_initialization_ids`
metadata plus `content_initializations(command_id,record)`, including an explicit empty inventory.
Null policy differs from a missing policy row. The singleton original record contains the immutable
profile/logical Root, source policy, local input paths, issuing epoch, first kernel/process budget,
intent digest, optional full original capture and optional actual first completion. Missing rows,
changed policy/source, broken capture coverage/digests or invented completion refuse publication.
Current state or bytes never fill an old Unknown or renew its first budget.
Completed Archive histories additionally require their declared original source CAS and Archive
destination. Exact repeated operations from independently verified Controller projections in the
same Replica profile may share validation; all original records, cleanup and dependencies must
match. Another profile, standalone duplicates and conflicting records refuse.

An initialization candidate uses application-initialization/v0.1 and its context-bound
application-snapshot/v0.1. Its receiver predecessor is empty. The complete original source preserves
the checkpoint bytes, command sequence, hot tail, Archive/Catalog and actual floor; no prefix event
or prior checkpoint is synthesized. Accepted state retains its exact initialization anchor. Later
ordinary application-commit/v3 envelopes retain that anchor unchanged. Member-local initialization
operations are independent of Raft Snapshot import and stay exact through cold restore.

Deployment input components explicitly cover selected certificate, key, CA and per-listener
identity/authorization profile files. Common policy hashes file bytes independently of relocated
paths. Restore creates no service, Node, device, model or simulator and does not reread an already
accepted application's original source. Software process exit and restored bytes prove no physical
stop, execution continuity, global erasure or overall SoftwareComplete.

See [ADR-0195](../../../docs/decisions/0195-original-archived-source-initialization.md) and the
[deployment workflow](../../../docs/development/original-source-initialization-v0.1.md).
