# ADR-0216: Reuse only an unchanged original application data frame

Status: Accepted for implementation, 2026-10-26.

Actual two-domain HA exercised repeated complete view reads while Node facts and the coordinator
continued committing. Re-reading and hashing the entire retained event/command/checkpoint tail on
each observation increases work with history, even when the accepted data has not changed.

The original private Raft SQLite connection may retain one successful `read_view` result. Before
reuse it samples actual `total_changes`, `data_version`, `schema_version`, `user_version` and journal
mode, then samples again after copying the frame. Same-connection writes, rollback, foreign commits,
DDL and generation/journal changes invalidate reuse. A fresh read uses one actual SQL snapshot and
is retained only if the original witness stayed unchanged through Commit. An in-flight transaction
or failed read clears the witness and never publishes a reusable candidate.

Only clones sharing the same actual private connection share this ephemeral frame. New connections,
restart, Snapshot import and serialized data do not acquire it. Original source-initialization checks
still execute after every read. Actual TLS/principal/leaf permissions, source PID/incarnation,
membership, epoch/quorum, current content/byte checks and original deadlines remain fresh and
independent; a cached data frame grants none of these.

This changes neither application/Node/Memory/event contracts nor any storage/recovery format.
The original tables, histories, Unknown and budgets remain exact. Actual SQL counterexamples cover
same-connection Commit/rollback, an independent writer's changed/corrupt metadata and DDL without
row-counter changes. The complete HA and content gates still must pass; data reuse alone does not
establish execution, device stopping, hard realtime or long-term SLO.
