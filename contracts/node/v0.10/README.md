# Node contract catalog v0.10

This catalog retains wire session `roboguide.node-protocol/v0.4` and adds explicitly negotiated
`roboguide.node.v0.7` / `roboguide.node-config/v0.8` Memory continuity. The safety-capable profile
is `roboguide.node.v0.8`; it adds the attempt-bound `Execute.execution_generation` field used by
the physical-stop evidence contract. Contracts v0.4–v0.6 remain explicit compatibility profiles.
An old Controller cannot silently accept a safety profile or infer support by ignoring an unknown
wire field.

`MemoryImportQuery` has an independent durable command identity, original import/content/provider,
generation, receive-relative budget, and current Controller epoch/digest. Controller commits its
budget/outbox first; Node commits admission before its once-only readonly query. An interrupted
query identity never runs again. A new generation consumes the original nonrenewable session.
All receipts retain normal application-Commit-before-ACK semantics.

The original instance comes from a configuration-fixed readonly HTTP GET before import. Provider,
workflow, connections, complete invocation and original instance are journaled before side effects.
The v0.8 import workflow must return a complete typed original-import result at its configured
`import_result_pointer`; HTTP acceptance by itself is insufficient. Query results use the same
[provider result schema](../../memory/import-query-v0.1/provider-result.schema.json).

Only durable exact-original `completed` or `rejected_before_effect` resolves an Unknown. Missing,
not-found, changed instance/contract, late or changed-ownership evidence cannot activate old work.
The Node republishes immutable completion metadata without importing payload bytes again. Replica
Imported remains history, separate from current availability, authorization and Task satisfaction.
Legacy retry publishes no fresh replica evidence and cannot grant an availability lease.

Node journal storage v7 uses version-qualified authority tables and readonly legacy views. Earlier
v6 binaries attempt writes to those views and stop before connecting or executing a local workflow.
The persistent v7 marker also prevents an old `user_version` assignment from downgrading authority.
Controller wrapper v31 and Memory command event codec v22 independently fence earlier software.

See [ADR-0105](../../../docs/decisions/0105-memory-reconciliation-authorization-and-spatial-provider.md).
Group authorization, revocation/GC, active-map and spatial fusion are subsequent required parts of
F4; this protocol slice alone does not complete that phase or establish physical continuity.
