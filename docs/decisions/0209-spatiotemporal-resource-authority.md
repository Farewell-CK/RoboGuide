# ADR-0209: Declared spatial footprints and original Control time calendars

Status: Accepted and implemented; local software-process acceptance passed on 2026-10-25.
See [the acceptance](../development/acceptance/2026-10-25-resource-topology.md).

Resource `units` remains the original minimum declared capacity. A Space or Time label alone
does not describe intersecting regions, openings, or the lifetime of an unresolved execution.
Two different ResourceIds may describe the same physical region. Resource-ID exclusion therefore
cannot be the only conflict check. No device inventory is available or introduced by this change.

An explicit `roboguide.resource-topology/v0.1` registry belongs to Control. A recovery-admin
installs complete revisions through the ordinary original-principal, expected-revision,
idempotent registry transaction. Each entry references an already deployment-declared resource
and its Node owner; accepting this registry neither registers that Node nor creates a resource.
Space entries declare integer-millimetre axis-aligned bounds, a nonnegative clearance margin,
and an exact conflict-domain/frame/anchor revision. All entries in a conflict domain use the
same frame and anchor. Unknown transforms cannot establish disjointness. Inclusive spatial
contact conflicts; integer overflow refuses. Arbitrary footprints may be conservatively enclosed
by these bounds; geometry is a deployment declaration, never measured occupancy or localization.

Time entries name an explicit shared `time_domain`: aliases with distinct ResourceIds still
conflict in that calendar. They declare bounded, canonical half-open availability intervals in this Controller's
receive-time clock only. Space entries may also constrain availability. A remote wall-clock
reading cannot be inserted as local time authority. Federation must translate bounded uncertainty
before local admission. Original Task timing/duration and the Control calendar provide the booked
interval; a Time resource requires a bounded original interval. Future selection may start at a
declared opening. Planned completion is not release evidence: an active or Unknown original
reservation continues blocking actual Commit until its normal stop/revoke/release gates complete.

Canonical v0.8+ plans intentionally contain no model-authored duration estimate. The explicit
deployment registry therefore declares `booking_duration_ms` and `booking_max_age_ms`. Application
passes that budget through the existing attributed duration port only for Tasks demanding Space
or Time. Its source names the exact original registry revision; receive time is the original
Mission acceptance, and the maximum age never resets on retry/restart. This is a declared booking
and overrun limit, never a measured duration prediction. Models cannot configure it. Missing or
expired budgets cannot acquire a guessed Time interval.

The stateless Scheduler consumes the registry in its immutable Control snapshot. It compares
all intersecting footprints, including different ResourceIds and jointly selected Roles, as well
as ordinary and quantitative occupancy. Normal/Group/recovery Commit independently recheck the
whole batch, current declared Node/kind, availability, future promises and exact reusable Context
ownership before quota, reservation or event mutation. Bind/Rebind/readiness/outbox retain the
same admission checks. An overrun or expiry does not free resources, renew a window or report
physical stopping. It prevents new admission and is exposed for the existing whole-Group safety
workflow. Local EAIOS retains final safety and Immediate How.

Each admitted reservation retains the exact original topology revision, declaration and interval.
Policy revisions cannot change or remove any declaration still held by active, Unknown or future
owners. Unrelated entries may change. Restore verifies original registry history, original owner
joins, interval bounds and pairwise spatial/calendar conflicts without recreating Node liveness.
Controller wrapper v58 and explicit recovery v0.27 fence older writers and retain the complete
previous v57/v0.26 contract. Node, MissionPlan, Memory and event codecs retain their meanings:
this is an additional Control-owned deployment constraint, not hidden new Node grant authority.

Supported local enforcement remains explicit: actual process-slot admission, immutable per-child
CPU/address-space limits and original engine resource locks. No software envelope is relabelled
GPU, device, hard-realtime or physical exclusion. Native acceptance exercises arbitrary declared
file/CPU software resources, conflicting/disjoint declarations, future windows, overrun, actual
owned-child wait, ordinary/Group/recovery admission, restart, SQL rollback, cold restore and genuine
old-writer refusal. Device and cross-host observations remain separate evidence requirements.
