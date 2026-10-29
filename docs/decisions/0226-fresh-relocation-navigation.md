# ADR-0226: Fresh relocation navigation command lifecycle

Status: Accepted; implemented with deterministic validation. Physical validation is separate.

## Context

An original Oracle action retains a completed-target latch across skill entries. A later
model-selected navigation to that same entity can return before recomputing arrival, while
the original finished sensor remains false. A relocation retry then consumes the skill budget
without executing the intended navigation. Acquisition already binds a fresh command lifecycle;
the relocation live binding omitted it. This is a Local EAIOS integration defect, not a reason
to change the Mission goal, assign a different robot or weaken official success.

## Decision

The existing explicitly enabled relocation completion profile also installs
`NavigationDispatchLifecycle` on its own live endpoint. Guard admission and original CrabAgent
mapping precede the lifecycle. Exact canonical contract, agent, tool call and increasing action
sequence bind each new command. Reused tool IDs, stale sequences, detached actions, closed
attempts and unrelated entities refuse before latch or cache effects. The per-attempt call-ID
set is bounded to 65,536 admitted navigation calls; exhaustion fails closed, never grants a new
budget. A later Task closes the original binding and gets its own contract.

For relocation, the model may select the canonical object or destination only as allowed by the
existing phase-aware Guard. The lifecycle clears only that action's `prev_nav_done` and
`skill_done`. It never writes completion, invokes `act`, steps the simulator, samples a route or
uses RNG. The next original navigation skill computes the actual result. An already-arrived
robot may complete without moving; clearing a flag is not evidence of arrival.

For a selected cached relocation target, read its current position through the original PDDL
entity-position getter. If that exact position differs from the cached entity position, invalidate
only the selected entry so the following original skill resolves it again through its normal
path. Stable entries, peer caches and the original pathfinder remain. Missing or malformed
current/cached positions fail closed before command mutation. This restores a current-target
command; it is not a new arrival tolerance or unconditional target resampling policy.

The live producer emits a closed Local How v0.20 envelope containing the unchanged versioned
live base profile and `fresh-relocation-navigation-command/v0.1`. The enabled frozen relocation
declaration is required. The adapter preflight and independent evaluation reader validate the
envelope and recursively validate its one original base profile; nested v0.20 is refused. Older
archives retain their original meanings, and older readers refuse the new envelope. Acquisition
retains its existing v0.19 preparation feedback and viewpoint policies. Plain mobility and native
EMOS retain their behavior. The loaded source manifest includes the actual lifecycle and live
binding modules.

## Boundaries and acceptance

No Core, MissionPlan, MI Prompt, Node capability, official PDDL, physics, target, seed, skill/world
budget or original EMOS source changes. Local skill observations, Mission completion and official
joint outcomes remain independent. Storage loss cannot manufacture completion. The repair restores
the opportunity for a genuine retry; it does not explain every initial pick/place failure or
guarantee benchmark success.

Deterministic tests cover exact raw/mapped calls, both relocation targets, arrival recomputation,
moving-object cache invalidation, stable cache retention, peer and attempt isolation, original
live Task reuse, stale/duplicate/closed calls, unavailable positions and independent artifact
readers. Physical regression runs must freeze the exact source/deployment identity and preserve
all failed inputs, first results and retry evidence in separate local experiment directories.
Failure-selected reruns cannot replace old outcomes or establish a whole-population success rate.
