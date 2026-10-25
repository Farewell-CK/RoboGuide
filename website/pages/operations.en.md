# Deployment and operations

Declare actual topology before starting services. Zero Nodes is valid; static examples cannot create device inventory.

## Before deployment

- Separate components, process groups, Hosts and control domains. Co-process components share actual binary/host/domain identity.
- Pin binary/config/contracts, SQL/CAS/store ownership and actual TLS leaves. Credentials reference local files, never documentation values.
- Define affected components, conservative fences, RTO/RPO targets and exit evidence for each fault. Targets are not measurements.

```bash
python tools/deployment/deployment_profile.py   --profile /absolute/profile.json   --output /absolute/new-private-receipt.json
```

This inspects declarations and local files. Add `--probe` only for a profile targeting your own services; it performs read-only TLS/GET and never Registers or Executes. See the [inspector](tools/deployment/index.md).

## Health, write recovery and alerts

Health, readiness, metrics and semantic satisfaction are separate. After write-disabled, inspect committed views and retain original generation, budget and ownership. A recovery-admin probe validates the original writer; manually clearing SQL fences is not recovery.

Prometheus/Alertmanager configuration comes from explicit endpoints/TLS/budgets. Short firing/resolved observations establish software paths, not long-term SLO or human acknowledgement. Follow the [observability contract](docs/development/observability-v0.1.md) and inspect queue/drop coverage.

## Quiescent backup and restore

A closed recovery profile declares original Controller, Node, Request, runtime-MI, four Memory databases, Replica and content stores. All actual writer/inode leases must be acquired before source reads.

```bash
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery backup   --profile /absolute/recovery-profile.json --destination /absolute/new-bundle   --command-id original-backup-id
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery verify   --bundle /absolute/new-bundle --manifest-digest sha256:PINNED_MANIFEST_DIGEST
PYTHONPATH=apps/recovery-tool python -m roboguide_recovery restore   --bundle /absolute/new-bundle --manifest-digest sha256:PINNED_MANIFEST_DIGEST   --destination /absolute/new-root --command-id original-restore-id
```

These are parameter templates, not completed deployment commands. Retain the digest independently and use fresh output roots. Restore preserves Unknown, budgets, history and active-reference/CAS consistency. It starts no services or fresh use permissions. See the [original recovery guide](https://github.com/Farewell-CK/RoboGuide/blob/main/apps/recovery-tool/README.md).

## Replicas and certificates

For explicit replication, check quorum/epoch, actual member leaves and original child spawn/PID/boot/start-tick/wait journal. Leadership transfer fences the source and confirms original Controller-child exit before sending the original V3 intent. Response loss stays Unknown; read-only observation does not repeat elections.

Certificate rotation, binary/profile upgrade, cold recovery and online content recovery are separate procedures. Metadata quorum does not establish CAS or device continuity.

[Application generation upgrades](docs/development/application-generation-upgrade-v0.1.md) explicitly use process v0.8, parent10 and recovery v0.25. Roll infrastructure first, then collect actual all-member support, original child waits and the complete predecessor before a separate activation. A failed application keeps the repair plane available. Observation never resends the original command; abandonment cannot undo activation, and same-schema repair refuses original/retired code. Complete recovery retains original upgrade, abort, budget and exit history without starting services.

## Incident order

Preserve logs/receipts → inspect exact identity/session/attempt → determine current write authority → check original State/Memory budgets → use formal read-only observation/reconciliation → recover after original stop/revoke and policy gates. Repeating work cannot resolve uncertain side effects.

Explicit scoped typed maps use MissionPlan v0.11, Node v0.14/config v0.15, preparation v0.3, Memory SQL v5 and recovery v0.26. Older generations cannot borrow new authority. See the [complete lifecycle](docs/development/scoped-typed-map-lifecycle-v0.1.md).

## Original resource bookings

The recovery-admin view `/v1/resource-topology` separates declarations, original reservations and unavailable physical occupancy. Time expiry enters the existing whole-Group stop/revoke workflow and preserves ownership until original release gates pass. Actual OS exit and Controller stop confirmation are separate. Current wrapper v59 requires explicit recovery v0.28, which retains all previous scoped-map and upgrade requirements; original older receipts keep their versions.

## Reviewed coordination

Use the [coordination contract](docs/development/execution-coordination-v0.1.md) to declare the original complete cohort before candidate output and independent Review. Receipt, terminal status, original process exit and device stopping are separate. Active v0.3 Readiness policies retain original gates through revocation and expiry; inspect plan-revisions and original stop/revoke evidence before resuming.
