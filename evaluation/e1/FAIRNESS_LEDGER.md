# E1 Fairness Ledger

This ledger freezes what is held constant, what intentionally differs, and
what prevents a run from entering a paired Formal E1 comparison. It never
uses a common episode id as a substitute for a common semantic workload.
Workload identity, pair comparability, Formal population admission, and
Habitat benchmark outcome are separate decisions.

## Protocol A: Native

| Dimension | Contract |
| --- | --- |
| Held constant | Habitat/EMOS checkout, dataset digest, episode/scene, embodiments, benchmark measures |
| Intentionally different | Complete systems: EMOS uses Leader plus Stage2; RoboGuide uses MissionPlan/Control plus deployment-selected Local EAIOS |
| Local policy | Record the actual policy surface on each arm. The current RoboGuide controlled bridge binds `nav_to_obj.target_obj` to the committed destination, rejects other actions through its Contract Guard, and uses the original EMOS wait skill without a model call for an unassigned endpoint. Native EMOS may ask that endpoint's model to choose `wait`. When `ROBOGUIDE_B1_GOAL_REGION_NAVIGATION=1`, RoboGuide also uses the ADR-0051 navigation-point resolver while native EMOS retains its original point resolver. |
| Claim boundary | End-to-end system comparison, not an organization-only ablation. These local-policy differences must remain visible in each run's evidence and the paired analysis. |

## Protocol B: Controlled

| Dimension | Contract |
| --- | --- |
| Held constant | Dataset digest, exact episode and scene, embodiments, semantic benchmark task, original EMOS Stage2 policy, CrabAgent, skill dispatcher, Oracle skills/actions, local model endpoint/name condition, and benchmark success predicates |
| Intentionally different | Global organization and assignment: EMOS Leader discussion versus RoboGuide MissionPlan/Match/Schedule/Proposal/Commit/Bind |
| Current RoboGuide bridge | The committed assignment replaces EMOS `group_discussion()` output (`AgentArguments`). It also binds one navigation tool parameter for assigned endpoints, guards selected actions against the committed operation, and makes unassigned endpoints passive. The original `MultiLLMPolicy`, `HierarchicalPolicy`, and configured skills still execute. These extra differences currently prevent the strict claim. |
| Success authority | Habitat `pddl_success` is benchmark success. Local skill completion, episode termination, RoboGuide Mission outcome, and process success are separate evidence |
| Sampling | Both arms use the original EMOS `OpenAIModel` provider defaults. Parameters that EMOS does not expose are not invented; endpoint, requested model, source versions, and run time are recorded |
| Messaging | Original CrabAgent `send_request` and class-level `message_pipe` semantics remain inside Stage2 and are measured when observed |
| Cancellation | RoboGuide cancellation is a system-level lifecycle difference; cancel acceptance never fabricates local `CANCELLED` |

The current bridge does not dispatch Oracle actions itself or implement a
second skill state machine. It does change the Stage2 decision surface for an
assigned navigation target and for an unassigned endpoint. Therefore a run
using this bridge **does not satisfy Protocol B's organization-only claim**,
even when the original EMOS source files and model endpoint match. Protocol B
needs a separately specified, validated local-policy equivalence condition
before it can be used for that claim. Do not relabel a Protocol A result as
Protocol B after seeing its outcome.

The optional ADR-0051 goal-region resolver is an additional Local How difference.
For Protocol A, each arm's run evidence must record its effective resolver,
implementation revision, and whether selection succeeded or failed. The existing
`stage2_identity` source-file comparison does not establish resolver equivalence.
Neither a matching dataset/seed nor a `PAIR_COMPARABLE` workload verdict may be
reported as evidence that an outcome difference came solely from global task
organization. A comparison that isolates organization requires a separately
frozen, equivalent Local How condition in both arms before either arm runs.

## Paired-workload admission

A pair is admissible only when both runners prove all of the following:

1. exact dataset digest, episode id, scene id, and embodiment set match;
2. the same benchmark semantic goal predicates are assigned to the systems;
3. the required Stage2 and skill source versions match, and the actual local
   policy differences are recorded under the selected comparison protocol;
4. the same official benchmark predicate defines `success`;
5. neither arm uses an unrecorded fallback backend;
6. the actual reset state is observed on both arms; equal episode and seed
   alone do not prove identical initial robot poses;
7. required raw evidence is complete enough to classify infrastructure,
   system, local-agent, and model failures without guessing.

Episode 51 exposes two official terminal predicates:
`any_at(any_targets|0)` and `any_at(TARGET_any_targets|0)`. Each predicate
means *some* robot is at its named target; the conjunction does not require
different physical robots or two simultaneous navigation executions. A
one-Actor plan may satisfy both if the actual terminal physical state does.
The current RoboGuide B1 path can preserve the full joint goal and obtain an
official result. Its one-run success is not a population success-rate estimate.

[`controlled-workload-v0.1.yaml`](controlled-workload-v0.1.yaml) records
semantic workload coverage and its historical evidence anchor. Its `ready`
status does not certify Protocol B local-policy equivalence, pair fairness,
Formal population membership, or a successful benchmark outcome. Those must
be checked from each new run's own evidence; failures of MI or execution
remain system outcomes, not automatic exclusions.

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
