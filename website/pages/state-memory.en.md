# State, Memory and the content data plane

## State: attributed current evidence

Records preserve object identity, Desired/Committed/Reported/Observed/Derived/Belief semantics, source, channel, schema, source time, receive time, TTL and optional confidence. Sources remain independent. The unified API federates reads over original authorities. Control uses explicit tenant/source/schema/TTL/conflict policies.

Belief requires a named producer and original lineage. Confidence is not variance; duplicate inputs and restart never refresh TTL. Independent source clocks cannot be directly compared with Controller receive time.

## Memory: content and permission lifecycle

Execution, Spatial, Semantic, Experience and Artifact content remains with the original provider. The catalog supports discovery; Artifact CAS stores immutable bytes; receivers retain provider-qualified import/placement/use evidence. Configured EAIOS workflows do not silently copy backend content into the Node ledger.

| Dimension | Governs |
| --- | --- |
| Scope | Semantic consumers of content |
| Visibility | Metadata discovery and content exchange |
| Placement | Evidence for an exact Node/provider replica |

`Local + Discoverable` is valid. Visible metadata never broadens consumption scope.

## From reference to current use

1. Plan v0.9 declares semantic inputs, not backend paths.
2. After Bind, Control selects the operation-owned receiving provider.
3. Persist original context identity, budget, session and policy before Prepare; ACK precedes original import or read-only query.
4. Current bytes/instance availability and use receipts gate Execute. Another Task's Imported history is not a fresh permission.
5. Normal Task release, whole-Group stop and handoff revoke their exact scopes. Unknown retains original IDs without reimport.

## Maps, references and recovery

Byte placement does not establish active-map selection or localization. Active Map retains original typed provenance, Node, Group, attempt and target. Worker/use/map/handoff references participate in original-provider conservative GC; tombstones precede unlink.

Quiescent recovery holds actual writer leases and validates declared SQL/content. Active references must resolve to the same bundle's CAS bytes. Missing mandatory tables/content or tombstone conflicts refuse publication. This does not cover undeclared global references or online cross-host replication.

See [State](modules/state.md), [Memory/recovery archives](archives.md) and [operations](operations.md).

Controller Artifact stores and Local Memory providers retain independent replica ownership; equal digests do not merge those responsibilities. Original Catalog/lineage, Snapshot and Archive references precede effects. Unknown retains references; complete original publication and the exact SQL receipt retire only the source pin. Receiving Archive bytes remain independently retained. Explicit storage v3/recovery v0.13 verifies known consumers, original cleanup journals/tombstones and content, refusing missing known binding rows. [Unpublished original Archive abort](docs/development/acceptance/2026-10-22-original-archive-abort.md) requires all actual original writers and its own cleanup budget, preserving partial files and receiving references. Source Artifact GC over all references, Raft Snapshot and online producers remain required. The preceding producer is recorded in the [original acceptance](docs/development/acceptance/2026-10-22-original-content-archive.md).

[Private receiving-owner reclamation](docs/development/acceptance/2026-10-22-private-archive-reclamation.md) adds separate retirement: all actual writers, an empty complete consumer inventory and the sole original producer precede the tombstone and terminal storage v4 seal. Independent GC intents precede unlink. Old Unknown commands only observe; new commands handle remaining bytes while retaining earlier uncertainty. v0.14 cold recovery verifies complete terminal metadata without requiring already deleted bytes. Source Artifact GC over all references and complete online-producer release remain required.
