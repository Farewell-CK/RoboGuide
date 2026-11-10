# MI reported-posture observer software acceptance, 2026-11-10

Status: software implementation and isolated checks passed. No field deployment,
robot contact, physical-effect proof or real-model MI planning acceptance in this run.
This record covers the observation component in the existing MI service. The complete
data-governance A–F plan remains subject to its other gates.

The existing MI service now owns an opt-in observer thread, durable queue and original
before-I/O evidence journal in its existing state database. New explicitly declared
posture Tasks freeze their expected posture, exact verifier policy, original MI plan and
actual outgoing body digest before Mission admission. The observer verifies original
Controller admission, tenant, Group, plan revision and complete unambiguous attempt set.
Only local Completed/AwaitingSatisfaction starts the ten-second observation window.
Two newer same-source/same-epoch native samples are required. Missing, stale, foreign,
changed-clock-domain or uncertain evidence stays Unknown/manual, with no action retry.

Independent verifier credentials require HTTPS, a distinct leaf and the Controller's
scoped `verifier` role. That identity can read required State/Mission views and publish
generic evidence, and cannot create Missions, cancel work or modify registries. Native
task delegation keeps its frozen profile and full objective; only the optional bounded
`expected_posture` metadata is added. The opt-in posture capability catalog and registry
template do not modify existing deployment catalogs or grant deployment authority.

The private process gate is
`integrations/robonix-task/tests/posture_verifier_process_gate.py`. Clean run evidence:
`/home/aios/rg-lab/data-governance-20261110/posture-verifier-mtls-gate-r4/result.json`.
It used actual mTLS Controller/Node children with private SQL, PKI and synthetic State;
no robot or Pilot endpoint exists in the fixture. Children were bounded by `prlimit`
to 2 GiB address space and 120 CPU seconds, then explicitly stopped.

Observed results:

- Verifier Mission creation, cancellation and registry mutation all returned 403.
- Positive evidence returned an original receipt with `applied=true`; Task Completed.
- After the original ten-second window, negative evidence returned `applied=true`;
  Task SatisfactionRejected and Mission Failed.
- Exactly two distinct original software attempts were dispatched, one per Mission.
  Observer restart did not add a dispatch or republish a completed job.

Selected Controller SHA256:
`1b853e50f1985ce00218262bef3b31131e5e278c93b6caba1661108aac5f4b02`.
Selected Node SHA256:
`35a20fc7dd0d0bf66e61cb606d943e62471ad1ec3fc77f6f4dda7070f5061f3d`.
Final integrated Controller builds require the normal final gate rerun; this private
gate does not certify later Rust changes or field readiness.

Focused final tests: 59 passed, covering worker lifecycle, same-boot recovery, cache
replay refusal, source/attempt/plan/tenant changes, missing/stale observations, SQL
publication delay, lost replies, admission ordering, service readiness/lifetime,
configuration and native metadata. Full MI/integration regression passed 737 tests
in 135.60 seconds before the last additional catalog-consistency test; that additional
test passed in the focused final set. Strict mypy passed all nine changed MI source/test
files; Ruff, format and function-documentation checks passed.

Evidence logs are in `C:/workspace/DEAIOS/lab/rg-data-governance-20261110/`:
`mi-posture-process-gate-r4.log`, `mi-full-regression-final.log`,
`mi-posture-worker-tests-final.log`, and `mi-posture-mypy-final.log`.

The initial full regression also exposed a previously recorded certificate-rotation
fixture failure. The original baseline `7fd7b2d1` reproduced the 404 using its unchanged
Mission sources: the same mutable urllib Request, reused through ambient proxy handlers,
changed the actual HTTP request form and later tried plaintext against TLS. Creating
fresh proxy-disabled HTTPS requests for the same logical body/key/principal fixes the
fixture. The unchanged baseline passed with that corrected transport. Production TLS,
route parsing, tenant checks, certificate revocation and original receipt semantics were
not relaxed. Evidence: `mi-rotation-baseline-7fd7b2d1.log` and
`mi-rotation-baseline-fixed-transport.log`. The full final regression is all green.

Current bounded scope: one role per posture Task, original plan revision zero, and an
unambiguous exact completed attempt set. Multi-role/revised-plan jobs remain manual
Unknown rather than choosing an attempt by timestamp. Uncertain publication retains
the original full input for manual read-only reconciliation; it is not automatically
resent with renewed measurement age. Historical execution-report Tasks are not rewritten.
