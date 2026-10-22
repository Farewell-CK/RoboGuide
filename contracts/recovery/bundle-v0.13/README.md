# Recovery profile v0.13

This explicit profile retains the v0.12 owner, known-consumer, complete Catalog and directory
contracts and adds Archive operations v0.2 / Controller wrapper v50 cleanup authority. It does not
change Node, Memory use, device stopping or online replication contracts. Original v49 copy intents
and their embedded checkpoints retain their original schema, exact bytes, input hashes and budgets.
Migration validates the complete original journal before adding the mandatory independent abort
journal and advancing its marker. Accepted missing records refuse replacement, even at zero events.

An explicit original abort command has its own bounded 1–30000 ms cleanup budget. It never renews
the original copy. Actual source CAS and Archive-root writer file descriptions exclude the managed
same-host synchronous copier. Their observed owner, device/inode, current Controller PID/start tick,
boot, time namespace and original observation are retained before the tombstone. These are software
writer fences; they establish no Node, child-exit, device-stop or physical-continuity proof.

The original cleanup intent is durable before the fence and tombstone effects. The tombstone is
durable before its exact source SQL receipt retires the source pin. Incomplete cleanup remains
Unknown. An expired cleanup can only observe its own original outcome; a distinct newly authorized
cleanup may independently fence the same still-pending copy. It does not modify either earlier
budget or repeat a copy. A final archive publication refuses abort and requires its original result
reconciliation. Aborted copy IDs never execute or compact again. Partial content and receiving
references remain; abort is not deletion permission.

The existing directory-aware bundle v0.4 retains both journals and original final/pending cleanup
tombstones. Verification refuses missing tables, changed input/budgets, conflicting publication and
abort, receipt/tombstone mismatch or absent current known consumers. Only a valid complete source
receipt changes a pending source-pin obligation. Restore receipt v0.13 preserves exact original
history and starts no service. Prior profiles refuse new cleanup authority instead of omitting it.

Actual maintenance uses `POST /v1/maintenance/archive/abort` with schema
`roboguide.archive-abort-command/v0.1`, cleanup `command_id`, `original_command_id`,
`original_intent_digest` and cleanup `budget_ms`. The actual authenticated global recovery-admin
and deployment supply caller, tenant and original destination. `GET
/v1/maintenance/archive/operations?command_id=...&kind=copy|abort` observes original evidence under
the same authorization. Query never copies or allocates a fresh effect budget.

The actual zero-Node gate is `check_content_archive_processes.py --abort-unpublished`; with an
explicit prior binary, `--upgrade-original` creates the original v49 copy using that actual binary
before the current writer migrates/reconciles it. File/Catalog and cold recovery gates retain their
original independent scope. Archive receiving-reference retirement/GC, complete reference coverage,
Raft/online producers and all remaining P1-P7 requirements remain mandatory. See
[ADR-0154](../../../docs/decisions/0154-original-unpublished-archive-abort.md).
