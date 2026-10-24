# ADR-0200: Bind Memory replica evidence to the consumer attempt

Status: Accepted and implemented; legacy evidence remains explicitly unavailable.

Replica placement evidence previously identified only a receiving Node and provider. That was
enough to describe a local byte copy, but it could not answer which TaskExecution owned the
import purpose. A Node or provider name must not be used to infer that ownership during State,
GC, recovery, or handoff review.

New generic and typed-map replica evidence may therefore carry an exact `TaskRef` and physical
`execution_id`. The Node builds this provenance from the canonical Memory import invocation. The
Artifact listener checks it against the durable Controller import intent before appending evidence;
the event log uses payload codec v33 for such records. State and Catalog retain the provenance in
the latest provider-qualified snapshot. Older events decode with `None`, which means unavailable
history and never a guessed Task or attempt.

This strengthens audit and reference reconciliation only. It does not grant import permission,
make a replica globally available, stop a device, or establish physical continuity. The original
import, provider, deadline, and current-use gates remain authoritative.
