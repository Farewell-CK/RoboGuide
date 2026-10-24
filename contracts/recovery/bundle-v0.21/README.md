# Complete Replica content reclamation recovery v0.21

Recovery profile v0.21 explicitly admits Root7 and retains v0.19/v0.20 meanings. Root7 components
declare `roboguide.replica-content-layout/v0.7` and mandatory nullable `restore_binding_digest`.
The original backing mode and all prior journals remain mandatory. Complete `gc_ids` and
`tombstone_keys` inventories cover `content_gc_commands` and `content_tombstones` SQL tables.
`stream_retirement_ids` also covers the mandatory `content_stream_retirements` table. Each release
retains its entire unchanged original stream and actual worker close or independently validated
original process-end observer, separate from the new bounded retirement clock. Unknown stays Unknown.

Each original GC record retains the qualified key, original byte receipt, complete inactive
reference digest, actual original file observation and nonrenewing process budget. Outcomes remain
independently `deleted`, `missing` or Unknown. A tombstone requires its exact first command and
permanently forbids new availability/acquisition; an active reference conflict refuses backup or
verification. Deleted historical bytes are not required. Actual files retained in a bundle are
independently hashed, and every active reference still requires same-Root exact qualified bytes.

Restore appends backing schema `roboguide.replica-content-restore-binding/v0.3`, source Root7,
original paired parent identity and prior raw binding digest. It preserves original operations,
outcomes and clocks, starts no service and grants no Node/device or physical-continuity authority.
Older recovery profiles refuse Root7 even when both new inventories are empty.
