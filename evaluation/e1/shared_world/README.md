# E1-I Shared-World Evidence

Multi-node shared-world integration evidence for Habitat-MAS mobility episode 51.

- Architecture, production evidence, semantic equivalence, and admission basis:
  [FINDINGS.md](FINDINGS.md)
- Machine summary: `summary.json`
- Runs: `runs/{run1,run2,single,negative,cancel}` — each contains controller
  DB/events, per-node journals + bridge stores, shared-world evidence
  (`evidence/shared-world-summary.json` is the single-world/single-reset
  authority), bridge/server/node logs, and the mode verdict.
- Scenario (frozen inputs): `scenarios/e1-shared-world-episode-51/` at the
  repository root (mission plans, node configs, runner, verifier).
- Workload taxonomy: `../WORKLOAD-TAXONOMY.md` + `../workload-taxonomy.json`.

Admission anchor for `evaluation/e1/controlled-workload-v0.1.yaml`
(`admission.status: ready`).
