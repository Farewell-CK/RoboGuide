# E1 Fairness Ledger

This ledger freezes what is held constant, what intentionally differs, and
what prevents a run from entering a paired Formal E1 comparison. It never
uses a common episode id as a substitute for a common semantic workload.

## Protocol A: Native

| Dimension | Contract |
| --- | --- |
| Held constant | Habitat/EMOS checkout, dataset digest, episode/scene, embodiments, benchmark measures |
| Intentionally different | Complete systems: EMOS uses Leader plus Stage2; RoboGuide uses MissionPlan/Control plus deployment-selected Local EAIOS |
| Local policy | May differ by design; direct Oracle is a RoboGuide native deployment option |
| Claim boundary | End-to-end system comparison, not an organization-only ablation |

## Protocol B: Controlled

| Dimension | Contract |
| --- | --- |
| Held constant | Dataset digest, exact episode and scene, embodiments, semantic benchmark task, original EMOS Stage2 policy, CrabAgent, skill dispatcher, Oracle skills/actions, local model endpoint/name condition, and benchmark success predicates |
| Intentionally different | Global organization and assignment: EMOS Leader discussion versus RoboGuide MissionPlan/Match/Schedule/Proposal/Commit/Bind |
| RoboGuide injection boundary | The committed assignment replaces only EMOS `group_discussion()` output (`AgentArguments`); the original `MultiLLMPolicy`, `LLMHighLevelPolicy`, `CrabAgent`, `HierarchicalPolicy`, and configured skills run afterward |
| Success authority | Habitat `pddl_success` is benchmark success. Local skill completion, episode termination, RoboGuide Mission outcome, and process success are separate evidence |
| Sampling | Both arms use the original EMOS `OpenAIModel` provider defaults. Parameters that EMOS does not expose are not invented; endpoint, requested model, source versions, and run time are recorded |
| Messaging | Original CrabAgent `send_request` and class-level `message_pipe` semantics remain inside Stage2 and are measured when observed |
| Cancellation | RoboGuide cancellation is a system-level lifecycle difference; cancel acceptance never fabricates local `CANCELLED` |

The Controlled adapter does not copy prompts, impose an invalid-output budget,
dispatch Oracle actions itself, or implement a second skill state machine.
Unsupported output, wait, finish, skill entry/exit, pre/post conditions, maximum
skill steps, local replanning, and failure propagation are therefore the
behavior of the checked-out EMOS source.

## Paired-workload admission

A pair is admissible only when both runners prove all of the following:

1. exact dataset digest, episode id, scene id, and embodiment set match;
2. the same benchmark semantic goal predicates are assigned to the systems;
3. the held-constant Stage2 and skill source versions match;
4. the same official benchmark predicate defines `success`;
5. neither arm uses an unrecorded fallback backend;
6. required raw evidence is complete enough to classify infrastructure,
   system, local-agent, and model failures without guessing.

Episode 51 currently exposes two official predicates:
`any_at(any_targets|0)` and `any_at(TARGET_any_targets|0)`. The present
RoboGuide C1-S0B production Mission commits only one navigation operation to
`any_targets|0`. It is valid boundary evidence but is not an admissible paired
Formal E1 workload because it covers only one of the two benchmark goals.

The exact admission state and candidate identity are frozen in
[`controlled-workload-v0.1.yaml`](controlled-workload-v0.1.yaml). Formal runs
must not start while its admission state is `blocked`.

## Recorded versus unresolved variables

| Item | Treatment |
| --- | --- |
| Dataset/episode/scene/embodiment | fixed and verified |
| Local Stage2 and skill source | fixed by external checkout version; source path/version recorded |
| Model endpoint/name | fixed per local configuration/spec; secrets excluded |
| Relay implementation version | record when the provider exposes it; otherwise unresolved infrastructure provenance |
| Sampling parameters | common original EMOS defaults; explicitly marked not exposed |
| System-produced subtask wording | intentional organization output; preserve verbatim as run evidence |
| Local action/skill sequence | record as evidence and metric detail |
| Global versus local calls/tokens | report separately and in total |
| Benchmark, local skill, episode, Mission outcomes | report independently |
