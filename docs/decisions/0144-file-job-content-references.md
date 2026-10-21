# ADR-0144: Bind local file-job inputs to Memory content references

Status: Accepted, 2026-10-21.

## Context

The provider already used actual worker and use-lease state as broad reclamation blockers, but a GC
decision could not identify which CAS object a local computation still needed. Reference kind
`execution` existed without a production writer. A process restart also had to preserve unresolved
input content without rerunning an admitted computation.

## Decision

Local `FileJobs` records a provider-owned `execution` reference for each distinct imported CAS
selector in the exact admitted invocation. The owner is a reserved `file-job:<sha256(execution_id)>`
identity, the generation is frozen from the original execution, and reference IDs additionally bind
the selector digest. Duplicate selectors in one invocation share one reference. Declared export files
outside this provider's CAS do not create Memory-content references.

The reference and job admission share the provider writer mutex. The job/worker journal remains the
execution authority; `content_references` is its GC projection. A thread-backed job releases its exact
projection only after its terminal result is committed and its file descriptors are closed. A
process-backed job first records the original PID wait result; absent or failed wait evidence retains
its CAS reference. A content-reference write failure aborts admission; if the job transaction fails
after reference creation, rollback/reconciliation removes the unowned projection.

At provider startup, unresolved original jobs rebuild their references from the immutable recorded
invocation and current original provider bytes. Completed jobs remove stale projections. For process
jobs, a persisted original exit receipt can retire the projection after restart; missing exit evidence
leaves the job `UNKNOWN`, retains the exact input references, and never reexecutes it. External
reference commands cannot claim the reserved `file-job:` owner namespace. Ordinary explicit
`execution` references continue to use their own public lifecycle.

ContentReclamation filters job references by the digest being tombstoned rather than blocking every
CAS object because some unrelated job is active. It still independently requires every original
provider revoke, actual worker/lease blockers, Active Map authority, and its own durable tombstone
before local unlink. No global or remote deletion authority is implied.

The existing provider v3 schema already stores the complete reference identity; no schema change is
required. Historical unresolved rows are reconciled from their actual journal. Old thread outcomes
remain immutable evidence and are not upgraded into process-exit proof.

## Validation boundary

Tests use actual imported files, SQLite, threads and owned POSIX child processes. They check
digest-specific GC blocking, revocation while a local reader is live, child cancellation and actual
wait before release, restart reconstruction for `UNKNOWN`, and no-repeat replay. These checks create
no Node, robot, model or simulator and establish no physical safety. Provider-to-provider Replica,
Archive, Controller catalog, cross-host deletion acknowledgements and global retention remain open
F4/F5 work.
