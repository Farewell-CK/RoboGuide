# Source authority v0.1

Public Controller routes install expected-revision, command-ID/subject-bound registries:
`POST /v1/state-policies`, `/v1/belief-policies`, `/v1/verifier-sources` (recovery-admin).
`POST /v1/verifier-evidence` admits only an explicitly enrolled authenticated producer.
Readonly views use the same route names; `/v1/state/providers` and `/v1/state/records` enforce source/tenant ownership.

Schemas close payload structure. Native validation additionally enforces same-tenant sources, contiguous
registry history, complete current Role/attempt coverage, immutable accepted-plan digest, bounded original
freshness, unique producer selection, PSD covariance, units/frame, command deduplication and atomic authority commits.

Belief policy admission is restricted to one raw Node semantic, at most eight named sources and
`belief_fusion` only. State consumer gates cannot select that producer-only decision. Scalar interpretation
uses the explicit conflict reducer; Gaussian CI uses covariance and deterministic convex weights.
Missing sources below `minimum_sources`, invalid schema, conflicts or unavailable covariance yield no affirmative value.
Consumers choose an exact schema and typed Boolean/enum/number condition. They never acquire resource authority.

Verifier verdicts bind an accepted normalized plan digest from `GET /v1/missions/{id}/tenant` and the
complete exact attempt set from the owning execution API. Unknown/provisional evidence cannot satisfy a Task.
`require_artifact` final positives require actual existing CAS bytes/length/digest (max 64 MiB).
Source-local time is retained; Controller receive time plus bounded validity governs freshness.
Continuous observation installs a nonrenewable initial grace before Execute delivery and enters whole-set
stop/hold on expiry, unknown/negative observations or producer revocation/revision. Physical stopping still
requires independent operation-owned evidence; a Cancel receipt does not prove stopping.
Late terminal Task/Mission evidence remains an incident and does not rewrite execution completion.

JSON Schemas are structural contracts; their acceptance alone does not establish physical truth,
source covariance correctness, hardware attestation or hard realtime behavior.
