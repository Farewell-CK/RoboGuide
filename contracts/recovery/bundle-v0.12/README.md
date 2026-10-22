# Recovery profile v0.12

This explicit profile keeps v0.11 Memory Snapshot and Replica contracts and requires Artifact
storage v3. Same-owner migration fences v1/v2 writers/tools. Its mandatory reference authority
inventory names every known original Controller software store; recovery cannot omit one, including
a partially published original binding. Wrapper v49 requires the original authority ID and complete
Archive journal even with zero operations. Missing accepted records refuse replacement. Inventory
does not grant tenant, Memory or device access.

Archive components use their actual root writer plus declared Controller/CAS dependencies. Original
SQL command, checkpoint, Catalog, source owner, caller, kernel budget and content requirements
precede copy. An independently owned receiving CAS keeps its own original Archive reference.
Complete publication verification precedes the exact source SQL compact receipt. Pending source
pins and completed receiving references are separate duties. Matching hashes elsewhere cannot
substitute for original bytes.

Bundle v0.4 adds bounded original directory evidence, including mandatory empty nested backend
directories. Verification repairs nothing and refuses missing/undeclared directories, reference
history, original consumers or required bytes. Restore receipt v0.12 creates only a new target with
the exact verified directory/file set; original IDs, timestamps, budgets and uncertain outcomes stay.
Restore starts no services or Nodes. A compact receipt does not permit deleting the receiver.

Controller `POST /v1/maintenance/archive` accepts explicit `roboguide.archive-command/v0.2` with
original `command_id`, positive `end_sequence` and `budget_ms` in 1–300000. The deployed absolute
`ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY` and actual authenticated recovery-admin supply destination and
caller. Normal local maintenance requires no nonterminal Missions or Unknown attempts; replicated
writers require a separate coordinated online protocol. Same command retries never copy or renew
effect budgets. Before source compaction, a pending original publication is verified and its
existing files/directories can be synced; a completed retry only observes. Incomplete copy remains
pending and fences another Archive command. Explicit copier-fence abort/GC and online majority/
Raft Snapshot/upgrade recovery remain separate mandatory implementation work.

Native gates are `tools/quality/check_content_archive_processes.py` (zero Nodes) and the actual
file Mission profile `check_memory_preparation_processes.py --process-workers --artifact-retention
--content-archive`. These are software processes, TLS, SQL and bytes, never physical continuity or
overall SoftwareComplete. See [ADR-0152](../../../docs/decisions/0152-original-archive-content-and-catalog-recovery.md)
and [ADR-0153](../../../docs/decisions/0153-artifact-reference-authority-inventory.md).
