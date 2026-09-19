# E1 Fairness — Reviewer Challenge List

Date: 2026-09-19. Companion to `e1-fairness-foundation.md`.
Each challenge is a question a systems-paper reviewer would plausibly ask
about the E1 paired comparison, with the current answer and whether the
foundation already covers it.

**C1. "You swapped the entire organization stack — why is this one fair
comparison and not two different systems on two different days?"**
Covered. Every non-organization surface is a declared, digest-bound
dimension; the pair manifest proves dimension-by-dimension equality per
pair from runtime evidence, not declarations. The allowed-difference
surface (organization axis, global tokens, assignment wording, wall time)
is enumerable and frozen in the population manifest.

**C2. "The planners did not even use the same model settings."**
Split, and that is the point: MI versus EMOS Stage1 *is* the independent
variable (MI runs `gpt-5.6-luna` at reasoning effort `xhigh`; Stage1 runs
provider defaults — see audit A14). The held-constant claim applies to the
Stage2 stack, which both arms run identically (`OpenAIModel`, no sampling
parameters, same env). The foundation classifies the first as
allowed-difference and the second as required-held-constant; today only
the classification exists — recording is Phase B/C work.

**C3. "Stage2 code could have drifted between the two arms of a pair."**
Partial. The Stage2 surface fingerprint is designed
(`stage2-surface-example.json`) and the pair validator gates on
`stage2_identity`, but no producer computes the digests yet
(integration plan Phases B/C). Until then the shared-checkout assumption
is explicit and unproven — disclosed in the audit (finding 10).

**C4. "Same seed means same episode, right?"**
Covered and explicitly refuted: workload identity is proven from resolved
episode/scene evidence, never from seed equality; equal seeds resolving to
different episodes produce the dedicated `seed_equal_episode_different`
exclusion (regression-tested).

**C5. "Did you cherry-pick which pairs to compare?"**
Covered structurally. Population rows are frozen before runs; each run's
evidence binds the population manifest digest at run time
(`population_digest_replay_is_rejected`), so a post-hoc manifest cannot
bless pre-existing runs. The pilot loop validates every started pair and
keeps every manifest, comparable or not (integration plan Phase F).

**C6. "You silently dropped the runs where your system failed."**
Covered. System success/failure never affects comparability
(`test_roboguide_system_failure_stays_fairness_valid`,
`test_emos_system_failure_stays_fairness_valid`); outcomes are recorded in
the pair manifest. Formal population admission is a separate authority and
is untouched by this module.

**C7. "How do we know both arms even loaded the same dataset?"**
Partial. The population manifest pins the dataset sha256 and the EMOS arm
already verifies it at run time (`resolve_episode_in_dataset`); the
RoboGuide arm's runtime verification is integration-plan Phase C. The
both-arms-drifted-together case is caught by declared-vs-observed checks
(regression-tested).

**C8. "Episodes have randomized agent starts — are the two arms even
running the same initial state?"**
Honest gap. The task config sets `randomize_agent_start: 1`; the EMOS arm
seeds it via `habitat.seed`, the RoboGuide bridge currently seeds nothing.
Recorded as a HIGH-priority audit finding with a proposed patch location
(integration follow-up on Codex-owned files). Formal pairs should not
start before the seed plumb lands.

**C9. "Same episode is not the same task — the goal semantics could
differ."**
Covered at two levels: `task_spec_identity` (task spec + config digests)
and `benchmark_authority_identity` (measure, implementation digest,
evaluator parameters) are required held constants. After the
semantic-ingress merge, the authoritative goal expression additionally
binds into B1 provenance (that branch's work, consumed here only as
identity facts).

**C10. "Is the verdict itself trustworthy, or one more hand-written
check?"**
Covered. The validator is deterministic, typed, and reason-coded; manifests
carry canonical self-verifying digests; 41 adversarial regressions pin the
anti-patterns (unavailable-as-equal, unknown-as-mismatch,
success-conditioned admission, tampered digests, cross-population replay,
unlisted dimensions). Pair manifests are standalone auditable artifacts.

**C11. "Why is simulator drift only a warning? That changes physics."**
Documented rationale: both arms of one pair share one machine and one
checkout, so drift indicates a between-pairs environment change — a
reproducibility diagnosis, not a within-pair unfairness. Upgrading a
field to held-constant is a population-manifest revision, deliberately not
a code path.

**C12. "Where does fairness end and benchmarking begin?"**
The hard boundary: fairness answers "same workload?"; formal population
answers "does this run count in statistics?"; benchmark tri-state answers
"did the benchmark succeed?". Three artifacts, three owners, no code path
crosses them (enforced by the module never importing or writing the other
authorities).
