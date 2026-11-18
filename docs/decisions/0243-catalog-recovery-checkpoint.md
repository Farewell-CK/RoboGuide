# ADR-0243: Transactional Catalog recovery checkpoint

Status: implemented and isolated-process accepted on 2026-11-09; no field deployment.

Controller storage v2 is an explicit standalone profile. It preserves the original event
table, application checkpoint, command journal and Catalog archive contract from v1. It
adds a bounded recovery projection containing the exact original Catalog/localization
envelopes, their ordered digest, coverage sequence and global timestamp high-water.
It supplies recovery input, not another source of task, tenant or execution authority.

State builds the projection once by validating the original application checkpoint/journal,
then streaming the original archived prefix and complete contiguous live tail under its actual
SQLite writer lease and one exclusive transaction. Failed
migration rolls back the version fence and projection together. Every subsequent event append
advances the coverage and clock in that same event transaction; relevant envelopes are added
with their exact original bytes. Rollback cannot leave a newer projection than the source.

Normal recovery checks the v2 storage marker, complete projection tables, closed head schema,
coverage against the original event high-water, bounded row count/bytes, ordered digest,
original source envelopes and existing publication/adoption semantics. It reads only the
protected Catalog records, not historical telemetry. Missing or corrupt projection data
fails closed instead of silently rebuilding. A separate full-history scan remains available
for audit. Old telemetry corruption is detected by that audit, not promised by the fast
startup path; current checkpoint, protected evidence and original source validation remain.

The original unfiltered history APIs keep their output. No existing event is deleted,
relabelled or re-timestamped. Original archive prefix coverage remains independently checked.
New writers are required after migration: user_version=2 and the closed storage marker make
actual v1/legacy binaries refuse. The v2 input is explicit and never selected by default.
Rollback requires a quiescent pre-migration backup; removing the marker is not a rollback.

This first v2 profile is standalone. Replica materialization/export paths that do not carry
the new projection must refuse it; v1 and legacy replication behavior is unchanged. The
initial profile retains at most 16,384 protected envelopes / 64 MiB, matching the existing
Catalog archive bound; capacity exhaustion refuses the transaction rather than losing proof. Offline
SQLite backup retains all v2 tables. Generic recovery tooling and replicated deployments must
negotiate the new format before enabling it. No fleet deployment is implied by acceptance.

Retention is a separate next step: current State already has latest-per-key semantics.
Telemetry/duplicate content and command-journal retention require original decision-evidence,
Unknown, source freshness and idempotency coverage; this checkpoint does not authorize deletion.

Acceptance and measured limits are retained in
[the software acceptance record](../development/acceptance/2026-11-09-catalog-recovery-checkpoint.md).
