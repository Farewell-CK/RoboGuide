# ADR-0201: Expose the Federation session through an authenticated domain boundary

Status: Accepted and implemented; physical continuity remains unavailable.

The existing `FederationSession` was durable inside one Control checkpoint, but no deployed
service could receive a prepare, commit, or abort from another Control domain. That left the
cross-domain contract as an in-process API and made tenant and domain routing impossible to
audit.

The Controller HTTP service now exposes the session through three bounded routes:

- `POST /v1/federation/sessions/prepare`
- `POST /v1/federation/sessions/{transaction_id}/commit`
- `POST /v1/federation/sessions/{transaction_id}/abort`
- `GET /v1/federation/sessions/{transaction_id}`

Every request requires the configured operator mTLS profile, a recovery-admin grant, an explicit
tenant delegation, and a certificate grant that names both source and target domains. Each
Controller process has one startup `ROBOGUIDE_CONTROL_DOMAIN`; the request session must include
that domain and a different peer domain. The session now carries the immutable tenant identity.
The target and source do not share a resource registry: each domain stores the same immutable
transaction identity in its own checkpoint and independently validates epoch, digests and
deadline. A retry is idempotent only when the complete identity is unchanged.

The route mutates a private Controller candidate under the existing writer gate, saves the
complete checkpoint and command journal in the same SQLite transaction, and publishes the
candidate only after commit. Response loss therefore remains recoverable by GET; it never
replays a prepare or commit with a new deadline. A changed domain grant, tenant, source epoch,
or digest rejects the operation. The service does not send Execute, select Nodes, transfer
physical ownership, or claim device stopping.

The acceptance process uses two independent integration-server processes, two databases, real
TLS client certificates, a response-loss/retry window, tenant/domain rejection, epoch/digest
fences, and restart recovery. It creates zero Nodes, devices, models, simulators and hardware
evidence.
