# ADR-0202: Preserve original Federation service authority and receipts

Status: Participant authority implemented and process-verified; coordinator completion remains open.

ADR-0201 exposed participant session endpoints, but its first implementation did not yet
provide durable domain-policy versions, original command receipts or a cross-domain decision.
A local Committed row cannot prove the other domain committed. Two API clients writing the
same row to two services do not establish a Controller-to-Controller coordinator.

The participant service therefore retains a closed revisioned policy in wrapper v54. The
policy names the local domain, expected domain epochs, actual TLS leaf grants, stable principals
and exact tenants/domain pairs. Historical leaves cannot be removed or revived after revocation;
same-revision changes, rollback, local-domain replacement and missing policy after enrollment
refuse. Every effect repeats actual transport and policy checks under the application writer.
No unversioned static domain map is accepted.

Each original prepare retains its exact typed input, principal, original policy digest, local
receive time and actual same-kernel budget. A new request has a bounded deadline and begins only
in Prepared. Original command receipts survive phase changes, response loss and process restart;
retries observe those receipts without another SQL command or renewed budget. Uncertain writes
publish no private candidate. Legacy sessions retain their original raw data, but absent tenant
or original service authority never grants a new service effect.

GET reports participant-local phase separately from global readiness. Global dispatch readiness
remains false until a separately implemented coordinator proves both participants' decisions and
their current local resource/epoch authority. The endpoint neither allocates resources nor sends
Execute. Policy epochs are explicit deployment expectations, not observations of a remote Raft
leader. The current absolute deadline profile is verified only on the same host; cross-host clock
translation and the durable coordinator remain required N6 work.

The software gate must independently inspect actual SQLite receipts, prove zero registered Nodes,
and exercise original retries after unrelated commits, dropped responses, rollback, restart,
deadline expiry, wrong domain/tenant/principal, policy revocation/rollback and old-writer refusal.
No device, model, simulator or physical continuity is created.
