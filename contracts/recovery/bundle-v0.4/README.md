# Recovery profile v0.4

This explicit profile preserves v0.3's component, replica placement and runtime-MI semantics and
adds the reference Memory provider's complete physical writer contract. The closed
[schema](profile.schema.json) does not register Nodes, grant content permissions or start services.

Each Memory root must contain its original Memory SQL user_version 3 and exact
`roboguide.memory-provider-writer/v0.1` marker. Backup freezes the original `writer.lock`,
`.memory-use.lock` and the actual inodes of `memory.sqlite3`, `memory-use.sqlite3`,
`file-jobs.sqlite3`, and `active-map/active-map.sqlite3` before reading any store. An active main or
child connection, even through a hardlink alias, refuses backup. All four databases are mandatory,
including explicitly inactive empty stores. Content and declared file trees remain hash checked.

Restore retains every original schema/row, UUID, budget, provenance, uncertainty and tombstone.
Private empty lease files let an unopened restored tree be frozen again; they provide no execution
or stop evidence. Restore receipt v0.4 retains explicit Artifact replica placements, including an
empty list when none were declared. Retrying a completed command only checks its original result.
Old tools refuse v0.4; old profiles refuse v3 Memory storage. This is quiescent declared-store
recovery, without automatic online HA or physical continuity.
