# ADR-0239: Explicit online adoption of static Node certificate trust

Status: Implemented and isolated-process accepted on 2026-11-09; field deployment pending.

The field MI entry uses authenticated tenant scope, while older field Nodes used a
static Node-to-leaf map. Ordinary lifecycle register commands cannot initialize Control
authority from that transport-only profile. Automatically assigning tenants from Node
names or dropping the MI tenant selector would cross the authority boundary.

Add recovery-admin POST /v1/node-identities/adopt with a closed adoption command. It
names the registry, supplies the exact original static map and explicit tenant/domain/
expiry for every existing binding. It cannot add, remove or replace leaves. Trusted
loopback without certificate trust cannot adopt. Control validates the complete original
Mission, Actor and retained pre-Bind resource ownership before installing a private
candidate. Integration independently compares the candidate's original map to its actual
current policy. The existing application writer and SQLite Commit precede publication.

An adopted registry uses v0.2 with a required complete original adoption receipt.
Original typed register receipts, principal and acceptance time remain immutable through
rotation/revocation and restart. A duplicate command returns the original receipt and
may retry publication without rewriting acceptance or SQL; a conflict is refused.
Prior readers reject the new registry generation/closed field. Existing startup v0.1
lifecycle profiles and their ordinary commands remain supported unchanged.

On cold restore, the original static startup map may attach the saved v0.2 registry only
when its adoption receipt matches that exact original map. Subsequent registered leaves
and lifecycle changes come from the validated original checkpoint, never from a fresh
bootstrap. A changed startup map or stripped/downgraded receipt cannot authorize restore.
Explicit lifecycle startup still retains its original registry identity/projection checks.

No Node session, capability, resource, PhysicalEntity, hardware attestation or execution
is created by adoption. Actual TLS expiry remains checked independently. Existing live
sessions and reservations are retained; subsequent register admits new certificate
bindings through the existing hot publication path and actual Node authentication.

Domain owns the bounded complete adoption command and original receipt. Control owns
ownership validation. Integration owns current transport map comparison/projection.
The existing application/node_identity module owns HTTP Commit/publication and startup
reattachment; it does not create another gateway or authority journal.

Acceptance requires exact-map/permission/conflict/SQL rollback checks, retained pre-Bind
ownership, no static-profile escalation, two actual mTLS Node processes with the original
Task running through adoption, unchanged hot-phase PID/session, new Node execution,
cold restore under unchanged startup input and refusal by an actual prior Controller.
This is software acceptance, separate from robot behavior and hardware trust.

The actual current Controller and two Node processes passed this gate; the archived
2d6a043c Controller refused the v0.2 checkpoint. See the retained evidence and limits in
[the acceptance note](../development/hot-node-enrollment-acceptance-20261109.md).
