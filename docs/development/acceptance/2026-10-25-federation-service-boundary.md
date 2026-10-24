# Federation service boundary acceptance (software only)

The first ADR-0201 endpoint package is superseded by ADR-0202's original authority and receipt
fixes, recorded in [the current acceptance](2026-10-25-federation-original-authority.md). The
two-service checker acts as an external client. It does not implement a durable peer coordinator;
current service responses keep global dispatch readiness false.

ADR-0201 connects the durable Control `FederationSession` to the deployed Controller HTTP
listener. The listener accepts only the authenticated recovery-admin mTLS profile. The grant
contains explicit tenant delegation and `domain_ids`; the process has one configured
`ROBOGUIDE_CONTROL_DOMAIN`, so a caller cannot use a body or header to select an unrelated
Control domain.

The prepare, commit and abort routes run through the existing Controller writer gate and save the
whole wrapper checkpoint before publishing the candidate. `GET` is the reconciliation path after
a lost response. The immutable tenant, source/target domain pair, epoch, plan digest,
ownership digest and deadline remain part of the session; retries with a changed identity are
rejected and retries after expiry cannot renew the transaction.

The maintained process gate is:

```text
python3 tools/quality/check_federation_service_processes.py --output <evidence-dir>
```

It starts two real integration-server binaries with separate SQLite/CAS roots and independent
loopback TLS listeners, performs the cross-domain HTTP exchange, kills/restarts one process, and
reconciles the original transaction. It also checks tenant/domain/certificate rejection and
stale epoch/digest rejection. No Node registration, Execute, device, model, simulator or
physical claim is produced. The result is a software Federation boundary receipt, not an online
HA or hardware-continuity result.
