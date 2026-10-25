# ADR-0208: Scoped typed-map preparation and transitive lifecycle

Status: Accepted and implemented; actual software-process acceptance passed on 2026-10-25.
See [the acceptance](../development/acceptance/2026-10-25-scoped-typed-map-lifecycle.md).

ADR-0199 retains an original typed source during the first Group handoff. ADR-0203 does the
same for an original runtime revision. Their scoped generic Spatial children currently lose the
typed input used by the receiving provider's active-map lease guard. Normal preparation only
accepts the exact global discovery projection of a typed map. The old handoff also requires the
source Group's Mission to equal the producing Mission, so an actual consuming Group cannot
continue the lifecycle without changing production provenance. The software must support real
consumption, repeated scoped transfer and revision while preserving the original map.

The original `MapArtifactManifest` remains immutable production provenance, including frame,
anchor, source Mission/Task/attempt, provider/owner, original revision, lineage, contracts and
actual bytes. A scoped Memory child has its own selector, exact consuming Group scope and
creation time. It is not a new computed map and does not replace the original map's revision or
production identity. Equal bytes alone cannot connect these authorities.

Explicit Memory preparation v0.3 retains both the complete scoped generic child and its original
typed map in the existing `typed_map` field. Existing v0.1/v0.2 retain exact-projection semantics.
The new validator permits only the original immutable projection with a different child selector,
exact receiving Group scope and monotonic creation time; all other metadata must remain exact.
Application produces this context only from an actual retained handoff/revision child matching
the current Catalog and original root. External generic Spatial publication is not such evidence.
Every later Task gets its own original preparation/import/current-use authority.

Handoff v0.3 allows an actual consuming source Group or a previously scoped child. The original
source preparation must match the exact current parent and root typed manifest. Both Groups'
actual complete memberships/attempts, current tenant/principal/policies, independent original
stop and all original provider revoke proofs precede child publication/target admission. Existing
v0.1/v0.2 semantics are unchanged. Scoped runtime revision v0.5 extends the same check to an
already-scoped parent; v0.4 remains the original global typed-source declaration. Review includes
the complete original declarations. Producing and consuming Mission identities never collapse.

Control owns source/target ownership and current permissions. Application retains immutable
cross-stage evidence and performs normal Control admission. Runtime owns attempts/outbox. Node
persists the original prepared context before import and Execute. The configured provider owns
actual bytes/current use/active-map/worker and reclamation. Active-map selection/query requires
the current receiving preparation's original typed root and exact Node/Group/attempt/use lease;
discovery metadata, a historical import or an old active head cannot supply that permission.
Normal selection under a fresh receiving lease may replace an old revoked selection; that is
not physical handoff or localization evidence.

The new interpretation is explicitly negotiated as Node Contract v0.14, config v0.15 and Node
wire catalog v0.17, with provider availability v0.2. Earlier declarations cannot receive v0.3
preparation. Controller wrapper v57, Node journal v15 and opt-in reference Memory SQL v5 fence
older writers while retaining original v56/v14/v3-v4 histories, permissions and budgets. Explicit
recovery v0.26 includes the complete previous recovery contract plus these authorities. Restore
does not start services, invent availability or renew original time/permission. Old histories
remain in their original interpretation. MissionPlan v0.11 adds the semantic Memory requirement `source_contract=scoped_typed_map`.
It names the retained source/scope meaning and never a Node, backend, path or transport choice.
Control matches such Roles only to explicitly supported Node/provider declarations, so an old
receiver cannot be selected and fail later while a compatible receiver exists. v0.8-v0.10 retain
all original meanings and canonical bytes; scoped data authority cannot be hidden in those plans.
New v0.3 handoffs/v0.5 revisions require this semantic target declaration. No device inventory is added.

The complete B module includes exact source resolution, Control/policy gates, provider import
and active-map consumption, actual original stop/revoke, repeated handoff/revision, restart/cold
restore, reference retirement and real reclamation. Current active/Unknown readers and pending
handoffs remain GC blockers; retirement needs original-provider evidence and never a caller
assertion that references are empty. Tests cover forged scope/root/frame/owner, old contracts,
SQL rollback, late original ACKs, restart, old-writer refusal and nonrenewing budgets.

Native acceptance uses actual attributed map files, CPU map/hash processes, maintained software
Nodes, real mTLS/SQL/CAS and independent file/map verification. No external model, simulator,
experiment or device is introduced. Software stop and map use never prove physical stopping,
localization, physical transfer, hard realtime, cross-host continuity or SoftwareComplete.
