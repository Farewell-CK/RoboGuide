# Federation participant HTTP v0.2

ADR-0202 defines participant-local evidence. No endpoint grants global dispatch, selects Nodes or
reserves resources. The durable Controller coordinator and cross-host time translation remain N6 work.

The operator listener must use real mTLS and a recovery-admin grant. Each request selects one exact
tenant. `ROBOGUIDE_CONTROL_DOMAIN` and `ROBOGUIDE_FEDERATION_DOMAIN_GRANTS` must both be set; the latter
names a closed revisioned `authorization.schema.json` policy whose `local_domain` matches the
configured domain. Actual leaf digests and stable principals must also match the operator grant.
Policy epochs are deployment expectations, never independent live remote-Raft proofs.

| Method / path | Body | Result |
| --- | --- | --- |
| POST `/v1/federation/sessions/prepare` | `prepare.schema.json` | Original participant Prepared receipt |
| POST `/v1/federation/sessions/{id}/commit` | `commit.schema.json` | Original participant Committed receipt |
| POST `/v1/federation/sessions/{id}/abort` | `abort.schema.json` | Original participant Aborted receipt |
| GET `/v1/federation/sessions/{id}` | none | Current local phase and original-receipt availability |

Prepare accepts only Prepared and an original local deadline within 300 seconds. Its original kernel
budget, policy, principal and full typed input survive restart. Exact retries return the original
receipt even after phase changes or expiry; this is historical observation and does not renew work.
A new Commit/Abort requires original current policy, principal, epoch/digests and remaining budget.
Unknown or absent original service authority never means no side effects. A legacy tenant-free
session stays historical; callers must not infer a tenant from it.

GET reports `local_commit_current` separately from `global_dispatch_ready=false` and
`remote_decision=unavailable`. The current deadline profile is accepted on the same host only.
Wrapper v54 fences previous writers even when policy enrollment created zero events or sessions.
Original v53 standalone history migrates without inventing service receipts; replicated upgrade
and cold-recovery combinations remain independently gated.

JSON schemas check closed syntax only. Runtime still checks pair inequality, local domain, tenant,
policy history, actual certificates, immutable ownership identity and deadlines. Offline schema
acceptance is not authority. `tools/quality/check_federation_schemas.py` checks retained actual
process requests and policy history without making network calls or creating any Nodes/devices.
