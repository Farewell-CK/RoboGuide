# ADR-0074: Bounded independent live endpoints and cached condition observation

- Status: Accepted for opt-in implementation; new populations require physical release gates
- Scope: deployment adapter, Controller application preflight and evaluation launcher

## Problem

The legacy shared-world profile admits two independent Actors with a two-endpoint
start barrier, or one Actor sequentially reusing one endpoint. That profile cannot
represent sparse dispatch to a three-agent environment, nor a navigation Task
followed by a separately dispatched observation Task. Advertising manipulation
for every endpoint would incorrectly give a Drone an arm.

The deployed Perception configuration already computes visual detections on
reset and step. Its Gym wrapper filters policy observations but retains the full
returned observation in `_last_obs`. Calling sensors again to reconstruct that
data would add an unapproved observation path.

The original EMOS policy also interprets an all-zero previous action as a new
episode and initializes every agent. Under dynamic Task reuse this can restart
an active peer or give a completed endpoint its previous Task again.

## Decision

Add the explicit `independent-live/v0.1` deployment profile. Existing deployments
and defaults retain their legacy paths. B1 deployment v0.3 declares one to four
endpoint config files; endpoint-registry v0.1 binds their exact bytes, agent IDs,
Node identities, loaded robot classes, ports, durable stores, operation support
and capacity-one `space` resources. The child verifies actual robot classes and
readiness after its single original reset, before endpoints become ready.
Readiness and initial port validation cover that actual endpoint set, including
one endpoint; they never require a nonexistent second agent or port.

Deployment feasibility v0.5 carries this profile. Application preflight admits
at most 32 independent single-role Tasks, including sparse and sequential use,
without requiring all endpoints to be assigned. Current registration, Matching,
Scheduler, Commit, Actor binding, Task satisfaction and resource release remain
Control/Orchestration authorities. The adapter never evaluates the DAG to author
dispatches. Only delivered immutable Execution Session attempts activate it.
Coupled Tasks, Actor migration, duplicate slots, overlapping local attempts,
concurrent manipulation of one object and retained cancellation continuation are
unsupported. A bounded wait for the next dispatch does not advance Habitat;
expiry is explicit and creates no success fact.
The live Task bound is enforced before Controller plan acceptance for every
topology, including serial reuse. Legacy serial admission is not a fallback
around live limits. The matching live operation reader accepts up to four
source-bound manipulators; legacy full-coverage admission remains dual-endpoint.

The original joint actor and Gym step run once per physical step. Unassigned or
locally completed endpoints select the original model-free wait skill. Each real
Control attempt initializes its original CrabAgent exactly once. An instance
hook accepts only the same initialization arguments when the original zero-action
heuristic repeats; changed arguments fail closed. Hook removal precedes the next
Task's original initialization. Peer model history and skills are retained.
This is a disclosed Local How change, not an unchanged native EMOS arm.
Current peer contracts are a separate read-only projection refreshed at actual
binding, completion and reuse. Guard tool targets and local feedback use it
without changing the owner's immutable contract or resetting an active peer.
Original messages cannot target passive endpoints or transfer Control Tasks.

Relocation readiness covers only endpoints actually declaring relocation. Start
evidence v0.2 explicitly permits that subset while full world/navigation evidence
retains every configured agent. Node source digests, actual reset identity and
operation admission cross-check the subset. Drone support cannot be inferred
from another endpoint's loaded arm skill.

The optional existing canonical `observation.verify@v1` accepts the deployment
parameter profile `expected=detected(entity-ref)`. It reads only an already
returned original `DetectedObjectsSensor` result from the bounded wrapper cache.
It performs no model, motion, render, reset, sensor refresh or RNG call. Actual
loaded sensors/cameras are inspected before this capability is advertised.
The receipt binds attempt, invocation, semantic evidence, step, entity and sensor
scope. A global detector remains global. A negative observation completes data
acquisition only; unavailable data fails locally and cannot become false or true.
The entity lookup is a PDDL scene index: the existing `scene_obj_ids` mapping
provides the simulator object ID before the semantic offset is added. A missing,
invalid or out-of-range mapping remains unavailable without new sensor calls.
Neither result substitutes for affirmative task satisfaction or official PDDL.
Active perception/search is not implemented by this profile.

## Evidence and limits

Local How v0.9 nests the original chosen navigation/relocation profile and records
the registry digest, observation flag and idle policy. Assignment arrivals,
per-attempt Stage2 conversations, Guard/skill feedback, condition receipts,
live-session history, physical diagnostics and final official metrics remain
separate. Setup and execution exceptions attempt terminal diagnostic flush and
preserve the primary exception. Zero physical steps yield benchmark unavailable.

The single global Habitat step budget and original environment termination remain
unchanged. There is no renewed per-Task budget or extra settling phase. Completion
of all local session slots ends this local profile; it is not semantic Mission
completion. Only the original official metric supplies benchmark success.

The pair driver can privately copy explicitly declared native reset JSON write
targets in each arm. Other datasets/assets remain shared read references. This
prevents concurrent `w2j` reset writes to shared vendor assets; it provides no
general OS write sandbox. Prepare-only starts no services or world. Actual source
identity, robot/sensor readiness, reset comparison, three-endpoint physical
behavior and archive completeness must pass before a new population is released.

The local native README references a missing Task4 config filename. The available
three-agent `llm_multi_agent_mobility.yaml` and 100-episode dataset are supported
as a candidate deployment, not certified as the paper's Task4 mapping. That
mapping remains a population release blocker until original evidence resolves it.

No Core contract, MissionPlan, MI Prompt, vendor source, Formal admission,
Fairness Validator or official predicate/metric is changed.

## Validation

Deterministic tests exercise one to four endpoints, sparse and late dispatch,
same-Actor sequential reuse, exact-once model initialization, untouched peers,
real parent/child Pipe command relay, bounded waiting, early episode end, original
exceptions and terminal evidence. Actual Node templates and source builders fence
rehash attacks, stale reset poses and invented Drone manipulation. Cached visual
tests distinguish positive, negative, malformed and missing observations without
active sensors. Legacy execution and all existing admission/provenance tests
remain part of the release gates. These tests prove local code behavior, not
future model compliance or physical benchmark performance.
