# E1 Harness Readiness Audit — Pairing Conditions and Provider/Stage2 Fairness

Date: 2026-09-19 (overnight fairness-foundation round).
Baseline: `main@7d35893ea107af7899e3a0605cc07a6c4f05d27e`.
Scope: read-only audit of the existing E1 harness against the paired-run
admission requirements in `evaluation/e1/FAIRNESS_LEDGER.md`. No runner,
verifier, adapter, or scenario file was modified. Line numbers refer to this
baseline.

Verdict summary: the harness records part of the pairing surface, verifies
less, and has **no pair-level validation at all**. The RoboGuide arm wired
into the harness (`evaluation/local.yaml:50-64`) executes the **authored
static-plan path with a hardcoded episode**, so the 12-pair pilot cannot run
through the harness today. Every finding below lists file/line evidence.

## A. The fifteen readiness questions

**1. Which runners actually consume the seed?**
Only the EMOS arm. `evaluation/local.yaml:34-38` renders
`habitat.seed={seed}` (plus `iterator_options.num_episode_sample=1`,
`test_episode_count=1`, `shuffle=False`). The RoboGuide arm consumes no
seed: `scenarios/e1-shared-world-episode-51/run-shared-world.sh` never
references the harness seed, and `run-b1-roboguide.sh` carries seed 40 only
inside the frozen `b1-input.json`, which is never injected into the bridge
or the Mission Service. Seed equality between arms is therefore meaningless
today until the RoboGuide arm either pins the same episode deterministically
or consumes the seed.

**2. Which paths hardcode an episode?**
`run-shared-world.sh:155` (`--episode-id 51`) and
`run-b1-roboguide.sh:168` (`--episode-id 51`). Both RoboGuide scenario
paths are pinned to episode 51; the bridge itself filters loaded episodes
by exact id (`integrations/habitat-local-eaios/habitat_local_eaios/backend.py:129-137`,
uniquely-available check at 131-137).

**3. Which paths are still B2 static-plan?**
`run-shared-world.sh:112` (`PLAN="$SCENARIO/mission-plan.json"`) submits a
hand-authored MissionPlan to the Controller. The harness RoboGuide arm runs
this script, so the harness-comparison path is B2-style organization input.
`FAIRNESS_LEDGER.md` (paired-workload admission) and the frozen
`b1-input.json` (`forbidden_content: hand-written MissionPlan DAG`) make
this path infra/ablation only — **not** a Formal B1 arm.

**4. Which paths are the production B1 MI full chain?**
`run-b1-roboguide.sh` alone: Mission Service ingress (line ~183: POST
`/v1/mission-requests` with only the instruction text), real MI
planning/review, Controller submission, dispatch to the shared-world bridge.
It is standalone and is not wired into the eval harness
(`evaluation/local.yaml` points at `run-shared-world.sh`).

**5. How does the EMOS arm resolve the episode?**
Seed-driven iterator sampling (`num_episode_sample=1`, `shuffle=False`),
then identity is read from the official stdout "Episode Step Info" banner
with a fallback diff of EMOS's persistent episode-log step files
(`evaluation/src/roboguide_eval/systems/emos.py:514-553`). The resolved id
is looked up in the digest-verified dataset file
(`emos.py:608-630`, `resolve_episode_in_dataset`), producing scene id and
dataset record identity.

**6. How does the RoboGuide arm resolve the episode?**
By construction: the bridge loads the configured task config and keeps only
the episode whose id equals `--episode-id` (`backend.py:129-137`); the
resolved `episode_id`/`scene_id`/`episode_terminated` facts are written into
`evidence/shared-world-summary.json` (`shared_world.py:76-84, 96-97, 947`).
Robust, but the pinning argument — not the seed — is the selector.

**7. Does "seed equal but episode different" exist structurally?**
Yes. The pilot runner (`evaluation/e1/pilot-v0.1/run-pair.sh`) passes one
seed to both arms; the EMOS arm maps seed→episode by iterator sampling while
the RoboGuide arm always pins 51. For every seed except the one that
resolves to 51 (seed 40, verified in `episodes.json`), the arms run
different episodes **with no runtime detection anywhere in the harness**.
The fairness foundation's `seed_equal_episode_different` reason and the
population rows (expected episode/scene per pair) close this at validation
time; harness producers must be wired in to make it automatic.

**8. Which side verifies the dataset digest at run time?**
EMOS arm only: `resolve_episode_in_dataset` checks the actual file's
sha256 against the ExperimentSpec digest before any lookup
(`emos.py:608-630`; spec digest `evaluation/specs/e1/mobility-smoke.yaml`,
`dataset.digest`). The RoboGuide arm loads the same checkout path
(`ROBOGUIDE_EMOS_ROOT`) but never verifies the file digest. A dataset
mutation between arms would be invisible to the RoboGuide side.

**9. Which side observes scene identity at run time?**
Both, by different mechanisms: EMOS arm derives scene from the
digest-verified dataset record (`emos.py:630`); RoboGuide arm reads
`current_episode.scene_id` from the live environment
(`shared_world.py:81`). Cross-arm equality of the resolved values is
currently checked by no one.

**10. How is Stage2 identity proven identical?**
It is not. The only evidence is the whole-checkout `git rev-parse HEAD`
version probe (`evaluation/local.yaml:48`) and the shared checkout path
(`ROBOGUIDE_EMOS_ROOT`, `local.yaml:59`). No file-level digest set exists,
so (a) a checkout change between the two arms of one pair, or (b) an
uncommitted working-tree mutation, would go unnoticed. The Stage2 surface
design (`fixtures/e1_fairness/stage2-surface-example.json`) defines the
required file set; run evidence must carry its digests (see integration
plan Phase B/C).

**11. Can the Habitat config drift?**
Yes, silently. Both arms load the same root config
(`.../multi_rearrange/llm_spot_fetch_mobility.yaml`; bridge arg at
`run-shared-world.sh:153-154`), but nothing digests the resolved config
chain (`llm_spot_fetch_mobility.yaml` → `config_spot_fetch_mobility.yaml` →
habitat defaults). A mid-experiment edit to any chained yaml would change
both arms only if they run after the edit — a before/after pair would
silently compare different tasks. Override sets also differ today: the
bridge pins `habitat.simulator.concur_render=False` and
`video_option=[]` (`backend.py:123-125`), the EMOS arm pins seed/count
overrides and keeps `video_option: ["disk"]` from the yaml. Rendering-only,
but it must be recorded, not discovered.

**12. Are benchmark authority parameters observable?**
Yes, all in `habitat-lab/habitat/config/benchmark/multi_agent/config_spot_fetch_mobility.yaml`:
`must_call_stop: False` (measurements.pddl_success), `robot_at_thresh: 2.0`
(habitat.task), `max_episode_steps: 3000` (habitat.environment),
`end_on_success: True`, `success_measure: pddl_success`. The bridge even
duplicates `--max-steps 3000` (`run-shared-world.sh:157`). None of these
values is recorded into run manifests today; the population manifest schema
now carries them (`benchmark_authority.parameters`).

**13. Is embodiment only statically declared?**
Today yes: spec `context.robots: "spot+fetch"` is the only embodiment fact.
Runtime handles are observable on both sides (config
`agents_order: [agent_0, agent_1]` with agent_0=SpotRobot_no_semantic,
agent_1=FetchRobot_no_semantic in the task config; EMOS robot_resume text
sensor; bridge `agents_order` length check at `backend.py:141-143`) but are
not captured into any manifest.

**14. Are model/provider actually identical?**
Three separate surfaces must be distinguished:

- **Stage2 (must be identical, both arms):** both run the original EMOS
  `OpenAIModel` (`habitat-mas/habitat_mas/utils/models.py:21`), which takes
  the model name from `EMOS_LLM_MODEL` (line 26, default `gpt-4o`!) and the
  endpoint from the ambient `OPENAI_BASE_URL`/`OPENAI_API_KEY` via
  `openai.OpenAI()` (line 43). The EMOS arm pins `EMOS_LLM_MODEL={model}`
  (`local.yaml:42`); the RoboGuide arm pins **nothing** — its Stage2
  inherits whatever the invoking shell exported. In the pilot path
  (`run-pair.sh:17`: `set -a; source emos.env`) both arms inherit the same
  values, and the harness child env is
  `dict(os.environ) | configured_overrides`
  (`evaluation/src/roboguide_eval/process.py:283-292`), so equality holds
  structurally today — but nothing records or verifies the values, and
  standalone scenario invocations are unpinned.
- **Organization axis (allowed to differ):** EMOS Stage1 Leader and
  RoboGuide MI. MI uses its own client config: `gpt-5.6-luna`,
  `model_reasoning_effort = "xhigh"`, relay `http://101.43.45.215:8080`
  (`config/mission.toml:23-34`, B1 timeout 600 s at line 30). The EMOS
  Stage1 uses the same `OpenAIModel` provider defaults as Stage2.
  **Reasoning effort xhigh (MI) vs provider-default (Stage1/Stage2) is a
  real sampling-surface difference on the organization axis** — allowed by
  the protocol, but it must be recorded (it is absent from every manifest
  today).
- **Sampling parameters:** `OpenAIModel` sets no temperature/top_p/effort at
  all, so both arms' Stage2 run on provider defaults — trivially identical,
  and recorded as "not exposed" per the fairness ledger.

**15. Which parts of the 12-pair pilot cannot run today?**
(a) The harness RoboGuide arm is B2 static-plan + pinned episode, so pilot
pairs would not exercise MI at all; (b) the B1 MI chain is not integrated
into the harness (no seed, no per-pair input injection, no results-root
plumbing); (c) `ROBOGUIDE_B1_INPUT` is exported by `run-pair.sh:14` but
consumed by neither scenario script; (d) no pair-level validation exists, so
seed≠40 pairs would be silently mismatched; (e) the RoboGuide arm performs
no dataset digest verification; (f) Stage2/model identity is unproven (see
10 and 14). The population manifest + pair validator produced in this round
provide the missing validation contract; producer integration is planned
(see `E1_FAIRNESS_INTEGRATION_PLAN.md`).

## B. Provider / Stage2 fairness classification (with evidence)

| Surface | Class | Evidence |
| --- | --- | --- |
| Stage2 endpoint (`OPENAI_BASE_URL`) | MUST EQUAL | `models.py:43` ambient client; inherited from one `emos.env` per pair (`run-pair.sh:17`); unpinned on RG arm |
| Stage2 model name (`EMOS_LLM_MODEL`) | MUST EQUAL | `models.py:26`; EMOS arm pinned `local.yaml:42`; RG arm inherited |
| Stage2 sampling (temperature/effort) | MUST EQUAL (trivially: unset) | `models.py:21-70` defines none; provider defaults both arms |
| MI vs Stage1 model/effort | MAY DIFFER (organization axis) | `config/mission.toml:24-26` (`gpt-5.6-luna`, `xhigh`) vs EMOS Leader on `OpenAIModel` defaults |
| MI timeout/retry | RECORD (org axis) | `config/mission.toml:30` (600 s); `mission-service-b1.toml` grounding attempts |
| Stage2 CrabAgent/policy/skill source | MUST EQUAL (file digests) | same checkout today; surface defined in `stage2-surface-example.json`; no digests recorded yet |
| Habitat task/config chain | MUST EQUAL (content digest) | same root yaml both arms (`run-shared-world.sh:153-154` vs `local.yaml:21`); no digest recorded |
| Benchmark authority (measure, `must_call_stop`, `robot_at_thresh`, `max_episode_steps`) | MUST EQUAL | `config_spot_fetch_mobility.yaml` (`must_call_stop: False`, `robot_at_thresh: 2.0`, `max_episode_steps: 3000`, `end_on_success: True`); bridge `--max-steps 3000` |
| Habitat override deltas (`concur_render`, `video_option`) | RECORD | bridge `backend.py:123-125` vs EMOS arm overrides (`local.yaml:23-35`) |
| CUDA device | RECORD | both device 1: `local.yaml:47`, `run-shared-world.sh:139`, `run-b1-roboguide.sh:151` |
| Bridge step pacing (`--step-period-ms 20`) | RECORD (RG-arm implementation) | `run-shared-world.sh:158` |
| Episode termination policy | MUST EQUAL | `end_on_success: True`, `should_terminate_on_wait: False` in the shared task config |
| Agent start randomization | **MUST EQUAL — currently at risk** | task config `randomize_agent_start: 1` with ep51 `start_position [0,0,0]`; EMOS arm seeds `habitat.seed={seed}`, the bridge sets **no seed** (`backend.py:118-127` has no seed override), so start-state RNG differs between arms for the same episode |

### "Looks same, runs different" findings (precise paths)

1. **Seed semantics.** Same `--seed` argument reaches only the EMOS arm.
   The RoboGuide bridge resolves episodes by id filter, not seed, and never
   seeds the environment (`backend.py:118-127`). Combined with
   `randomize_agent_start: 1`, two "same-seed" pairs can start the same
   episode from different agent positions across arms.
2. **LLM client surface.** EMOS Stage1/Stage2 and MI share the relay host,
   but run three different clients: `OpenAIModel` (no sampling params,
   ambient env) vs the MI provider client (mission.toml, xhigh). Same
   endpoint family is not sampling equality; only the Stage2-vs-Stage2
   surface is a fairness gate.
3. **Config equality by file path, not content.** Both arms point at the
   same yaml path; nothing binds the resolved config content to run
   evidence, so before/after-edit pairs compare cleanly today and wrongly.

## C. Severity-ordered readiness findings

1. **HIGH — Harness RoboGuide arm is not a B1 arm** (A3/A4/A15a): authored
   plan, pinned episode, no seed. Formal comparison impossible through the
   harness until the MI chain is integrated.
2. **HIGH — No pair-level validation** (A7/A15d): wrong-episode pairs would
   silently enter results. Foundation exists (this round); producers needed.
3. **HIGH — Stage2 seed divergence via `randomize_agent_start`** (B table):
   same episode, potentially different start states across arms; bridge
   needs a seed plumb (Codex-owned file; integration follow-up).
4. **MEDIUM — Stage2/LLM identity unpinned and unrecorded on the RG arm**
   (A10/A14): pin `EMOS_LLM_MODEL`/`OPENAI_BASE_URL` in the arm config and
   record both values per run.
5. **MEDIUM — No config/content digests** (A11): task spec + resolved
   Habitat config digests must enter run evidence.
6. **MEDIUM — RG arm dataset digest unverified** (A8).
7. **LOW — Rendering/pacing deltas unrecorded** (A11/B): `concur_render`,
   `video_option`, step period.
8. **LOW — Derived metadata unused**: `episodes.json` is a frozen table
   outside any schema; supersede with population-manifest rows.
