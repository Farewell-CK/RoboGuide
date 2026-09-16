# E1 Fairness Ledger

Purpose: enumerate exactly what is held constant vs intentionally different in
each E1 protocol, and the unresolved confounders — never a bare "same
conditions". Evidence anchors: `emos_local_agent_boundary/TRACE.md`,
`habitat_local_eaios/c1_s0b/FINDINGS.md`, EMOS checkout sources.

## Protocol A — Native

| Item | Status |
| --- | --- |
| Held constant | Habitat simulator build, MP3D dataset, scene/episode pool, Spot/Fetch embodiments, Oracle skill layer (`OracleNavAction`), termination measures (`has_finished_oracle_nav`), success metric definitions |
| Intentionally different | **The entire system**: EMOS = Leader discussion → CrabAgent → Oracle skills; RoboGuide = Mission Intelligence → Control (Match/Schedule/Proposal/Commit/Bind) → roboguide-node → direct-Oracle Local EAIOS (current C1-S0 bridge) |
| Local layer | intentionally different (EMOS has local LLM loop; RoboGuide native adapter is deterministic) |
| Token accounting | EMOS global (leader+discussion) + local (per-tick) vs RoboGuide 0 LLM |
| Unresolved confounders | local-agent LLM variance counted into EMOS's score; cancellation/recovery semantics differ at system level (by design in Native) |

## Protocol B — Controlled

| Item | Status |
| --- | --- |
| Held constant | simulator, dataset, episode IDs, robot, scene, Oracle skills, termination, success metric, model endpoint/name (`emos.env` relay + `EMOS_LLM_MODEL`), **local policy = same EMOS CrabAgent** (via `--backend emos-crabagent`), same scene_description source (`get_task_text_context`), same EMOS prompt templates (hashes frozen per run) |
| Intentionally different | **mission organization only**: EMOS Leader (`group_discussion`) vs RoboGuide MissionPlan v0.7 + Control pipeline; global planning LLM calls exist only on the organization side |
| Local subtask boundary | `subtask_mode=natural-objective` (main): RoboGuide `objective` text plays the role of EMOS Leader's assigned subtask; `entity-grounded` kept as ablation |
| Unresolved confounders | 1. subtask wording level — RoboGuide objective is not Leader-authored; phrasing differences shift local interpretation (observed: `TARGET_any_targets\|0` entity choice, 223 vs 101 steps) 2. `message_pipe` peer-help: never triggered in single-robot runs; policy for multi-robot Controlled tasks undecided 3. model sampling parameters not exposed by EMOS `OpenAIModel` (temperature/top_p "not explicitly controlled") 4. model version drift on the shared relay 5. episode termination: RoboGuide satisfaction/cancel semantics can end episodes earlier than EMOS's own wait-termination — Controlled protocol must pin the terminal predicate 6. local retry behavior: CrabAgent invalid-output budget (5) is wrapper-owned, EMOS has none |

## Freeze requirements before any Controlled comparison run

1. pin relay endpoint + model name + record `prompt_freeze.json` per run (done by bridge)
2. pin episode list from the frozen E1 workload (no cherry-picking)
3. fix terminal predicate to `has_finished_oracle_nav` + pddl_success on both sides
4. record global vs local token accounting separately (harness already separates them)
