# ADR-0039: Mission Satisfaction Freshness Policy v1

- Status: Accepted for the Mission Front-half V1 correctness fix
- Date: 2026-09-15

## Evidence and choice

The raw `m1-relocate-then-verify`, `m2-map-then-localize`, and `m4-map-relocate-verify` records in
`evaluation/baselines/mission-front-v1-freeze/mission-front-20260915T034439Z-2455d073/cases/`
show Review requesting `verifier-evidence`, Repair inventing a required evidence age, and Review
then rejecting that age. Removing it violates MissionPlan v0.7. This is a missing policy source,
not permission to loosen parsing or suppress Review.

Option B is valid only where an admitted canonical operation explicitly establishes the requested
semantic result on success. `observation.verify@v1` currently describes observing *whether* a
condition holds: completing that observation is not a guarantee of a positive verdict. The
specialized localization proof is also not a generic Task-verifier ingress. Neither an operation
name nor a transport `Completed` fact proves an arbitrary requested predicate. No such contracts
are silently strengthened here. A Task whose complete outcome is performing a check/reporting its
result may use `execution-report` under the existing Mission acceptance rules; that must not be
rewritten as proof that the checked condition was true. Explicit independence requirements remain.

We choose option A for Tasks needing independent affirmative evidence: provide one configuration-owned
freshness policy to the three Mission stages, and validate generated verifier bounds against it.

## Decision

`[mission.satisfaction_policy]` supplies a nonblank versioned `policy_ref` and positive u64
`max_evidence_age_ms`. Omission means **no configured verifier bound**, never an implicit numeric
fallback. Malformed configuration fails startup. Mission settings freeze this value once; Planner,
Reviewer, and Repairer receive identical `satisfaction_policy` input with a content digest and
`source = mission-system-policy`, `time_basis = roboguide-receive-time` provenance.

The repository explicitly selects `roboguide.mission-satisfaction/receive-age/v1`, with 5000 ms.
This adopts the five-second receive-age acceptance window already exercised by the maintained
v0.7 Mission fixture. It is a bootstrap system-policy choice: tolerate at most five seconds between
RoboGuide receiving a verdict and Orchestration evaluating satisfaction. It is not inferred from
physical dynamics, model latency, task duration, user timing, or the verifier sampling clock.
Deployments needing another tolerance must configure an appropriate policy/value; the digest changes
even if an operator mistakenly reuses its reference. The value is not a universal safety guarantee.

Planner and Repairer copy this exact value for each generated `verifier-evidence` Task. Their existing
canonical parser, implementation preflight, and Catalog checks run before the additional policy check.
An absent policy or a different generated bound fails closed, including when semantic Review is
disabled. Reviewer checks the same provenance and must not call that exact policy value model-invented,
request its deletion/nulling, or turn a system-policy gap into a user clarification. This is not an
automatic approval: independent outcome, predicate, constraints, and other defects still need review.
An explicit user tolerance incompatible with the configured value needs a deployment policy decision;
v1 does not parse natural-language numbers into silent overrides. Review returns `RejectDraft` for
that policy conflict, rather than repairing indefinitely or weakening the user constraint.

`verify`, `confirm`, `inspect`, and `check` are not sufficient by themselves to select a basis.
Review evaluates what must be established and the operation's actual semantic guarantee. When an
affirmative predicate or independent confirmation is required and the execution report is insufficient,
use `verifier-evidence` with the supplied bound. Ordinary physical operations retain their existing
`execution-report` acceptance option. A required verifier must never be removed to evade policy.

## Scope and limits

MissionPlan v0.7 public shape, provider DTO, Grounding context, timing, clarification, and Core remain
unchanged. Policy is adapter input configuration, not an LLM-produced field or live inventory. Already
accepted/manual MissionPlans retain their declared values; this check governs Responses-generated
drafts and their review, not Controller acceptance or Runtime execution.

The policy reference/digest is visible in model request inputs; the canonical draft stores the exact
bound, not a new provenance field. Durable per-draft policy snapshots across service reconfiguration
remain future work; historical configuration/model-input evidence is needed to audit the original
source. No claim is made that final MissionRequest records alone contain that full policy history.

Generic verifier evidence production/ingress remains deferred. Semantic admission with a satisfiable
policy does not prove evidence will arrive; such Tasks can remain `AwaitingSatisfaction` in deployment.
State freshness is not automatic truth fusion, and Local EAIOS retains Local How.
