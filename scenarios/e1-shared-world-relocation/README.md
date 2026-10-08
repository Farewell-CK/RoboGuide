# Shared-world integrated relocation deployment profile

This directory contains a deployment-owned, offline-validated Fetch/Stretch
shared-world template for the generic `object.relocate@v1` operation. Both Nodes
retain their mobility operations and add the same integrated relocation workflow,
capacity-one `space` resource, local simulator lock, readiness route, and explicit
Habitat robot type. The planning profile exposes only abstract capability facts;
the execution profile exposes operation resource needs.

The common B1 runner now selects this deployment through `b1-deployment.json`.
It uses the original `llm_height_man.yaml` configuration and its 4,000-step
budget, run-local Node/MI configuration, and production Interpreter -> Planner
-> Reviewer/Repairer -> Controller -> Node -> original Stage2. The committed
MI configuration selects `gpt-6.1-sol`; Stage2 model/endpoint/credentials remain
deployment environment settings and must be frozen separately before execution.
No MissionPlan, task allocation, object source ID, or benchmark result is supplied
by this directory.

An explicit frozen `roboguide.e1.b1-input/v0.1` workload is required. Its episode,
scene, dataset revision/digest, seed and high-level instruction must come from the
selected original workload. The official relocation goal concerns objects at
destinations; a robot merely navigating there does not establish that goal.
Never reuse another run's `initial-location:*` IDs: the new actual reset supplies
them to immutable MI grounding.

Prepare the real deployment files offline, without services, credentials, models,
reset or execution:

```bash
ROBOGUIDE_B1_PREPARE_ONLY=1 \
ROBOGUIDE_EMOS_ROOT=/absolute/path/to/emos-baseline \
bash scenarios/e1-shared-world-relocation/run-b1-roboguide.sh \
  /absolute/path/to/new-prepare-dir /absolute/path/to/frozen-b1-input.json
```

Preparation validates the input and declaration, freezes original config identity,
renders transport endpoints, preserves capacity-one resources, and derives exact
registration/recovery snapshots. It does not prove live readiness or physical
success. Prepare directories are consumed archives and cannot later be reused as
execution directories.

After separately checking Provider identity, GPU ownership, dataset fingerprint,
ports and the execution protocol, the same command without `PREPARE_ONLY=1` is
the controlled B1 entry. Use a new directory; the runner performs exactly one MI
submission. Normal B1 port environment variables work for this deployment too.

Before starting MI or Controller, it verifies the actual loaded skills, one reset
at step zero, exact Node snapshot, frozen workload identity and the exact initial
object sources published to planning evidence v0.3. Failure retains the original
files and writes `relocation-preflight.json`; it does not invent PDDL truth or
change Formal admission. Relocation recovery declarations include all three
supported operations, with group stop and unsupported continuation. Retained
cancellation and navigation-specific goal-region profiles are rejected.

This wiring and its startup checks are offline-tested. A full production MI and
physical relocation preflight remains pending; do not treat preparation or
deterministic fixture plans as an experiment result or batch-readiness evidence.
