# Mission Front-half V1 Freeze Audit

Status: **frozen before the final real-model regression**

Audit base: `main@ec0e07d8b966b4a97fdc8d8e2f07ae109bd188ad`

Historical source set: `evaluation/mission_front_cases/baseline.yaml`

Frozen V1 set: `evaluation/mission_front_cases/v1-freeze.yaml`

## Why this audit exists

The original 29-case suite was created before the current MissionPlan v0.7,
Capability Catalog v0.3, ADR-0038 clarification semantics, and the latest
Control-side matching semantics had stabilized.

Before the final Mission Front-half regression, the suite was audited against
a stricter criterion:

> A user semantic requirement counts as supported by RoboGuide V1 only when it
> has a canonical, machine-readable carrier and the downstream system actually
> consumes or transports that carrier.

Free-text retention alone is not sufficient. In particular, putting a concept
only in `mission.objective`, a Task description, an Actor identifier, or a
free-text execution objective does not make it enforceable.

This audit was completed **before** the final freeze regression. The historical
`baseline.yaml` remains immutable so earlier evidence stays reproducible and
the freeze set cannot be confused with benchmark-after-results tuning.

## Enforceability rules used

A case is V1-supported only when every material user requirement can be mapped
through a real carrier chain such as:

- capability predicate:
  `requirements.capabilities[].constraints[]`
  -> Node capability profile / Matching;
- relative timing:
  `task.timing.*_offset_ms`
  -> Scheduler activation policy;
- semantic operation parameters:
  `execution_intent.parameters`
  -> canonical invocation / node integration boundary;
- bounded resource demand:
  `requirements.resources[].{kind,units}`
  -> node resource feasibility checks.

A case is deferred when a material requirement has no faithful V1 carrier.
The audit does not allow a weaker substitute merely because a language model
can describe it in prose.

## Result

Original diagnostic suite: **29 cases**

- V1 freeze suite: **20 cases**
  - 12 kept unchanged
  - 8 corrected before the final regression to remove unsupported semantics or
    provide missing canonical parameter provenance
- Deferred / future suite: **9 cases**

No production code, MissionPlan schema, Capability Catalog, or prompts are
changed by this audit.

## V1 freeze suite

### Kept unchanged

| Case | Why it remains V1-supported |
| --- | --- |
| `n1-single-relocate` | Complete `object.relocate@v1` source/object/destination semantics. |
| `a1-missing-destination` | Missing destination is Blocking; scripted clarification supplies it. |
| `a2-missing-object` | Missing object identity is Blocking; scripted clarification supplies it. |
| `a3-pronoun-reference` | Unresolved object/destination references are Blocking. |
| `a4-undefined-criterion` | Material target and safety-location ambiguity is clarified. |
| `a5-vague-scope` | Scope changes the Mission Task Graph and is clarified. |
| `i1-relocate-canonical` | Integrated relocation is fully expressible. |
| `i2-relocate-long-distance` | Distance does not require exposing Local How. |
| `i4-relocate-two-objects` | Two canonical relocation goals can be represented as ordered Tasks. |
| `c1-two-robots-parallel` | Logical participants and two independent goals are representable. |
| `t1-deadline` | Completion deadline has a canonical timing carrier consumed by Scheduler. |
| `m2-map-then-localize` | Map-build then localization verification uses canonical operations and dependency. |

### Gold-corrected before final regression

| Case | Pre-freeze correction | Reason |
| --- | --- | --- |
| `n2-mapping` | Remove explicit robot embodiment; keep warehouse map build + publish. | V1 can enforce map capabilities, not a generic “robot dog” embodiment class. |
| `n3-patrol-verify` | Replace patrol-coverage wording with “confirm every corridor extinguisher is in place”. | Preserve the verifiable semantic outcome without claiming a canonical patrol-coverage operation. |
| `n4-edge-inference` | Remove edge placement; explicitly name `pointcloud-analysis-v1`. | `compute.infer@v1.model` is required and has no implicit default; edge placement has no V1 carrier. |
| `h2-mapper-then-transport` | Remove explicit robot embodiment; preserve map -> relocate sequence. | Map and relocation semantics are canonical; embodiment class is not. |
| `t2-start-window` | Express start time as a relative offset from Mission acceptance. | MissionPlan v0.7 timing is relative and Scheduler consumes the offset directly. |
| `t4-compute-resource` | Remove edge/GPU subtype; explicitly name `defect-detection-v1` and request generic compute resource. | Model provenance and generic compute demand have canonical carriers; edge/GPU subtype does not. |
| `m1-relocate-then-verify` | Replace “take a photo to confirm” with “confirm sample condition is intact”. | `observation.verify@v1` can verify a condition; V1 has no capture-artifact operation. |
| `m4-map-relocate-verify` | Replace “take a photo to confirm shelving” with “confirm shelving state”. | Preserve map -> relocate -> verify semantics without inventing capture/report support. |

## Deferred / future suite

These cases remain useful research backlog, but they are excluded from the V1
freeze regression because at least one material user requirement is not
faithfully enforceable by the current semantic surface.

| Case | Blocking V1 gap | Future capability needed |
| --- | --- | --- |
| `h1-multifloor-assignment` | `stair-capable` / wheeled embodiment constraints have no Catalog attribute or matching carrier; “stand guard” also lacks a canonical operation. | Mobility / embodiment attributes plus an appropriate standing/guarding semantic operation if retained. |
| `h3-requested-capability-outside-fleet` | Wheeled-robot vs fly contradiction is semantically ambiguous and neither embodiment/action distinction has a V1 enforceable carrier. | Embodiment attributes and an explicit contradiction policy. |
| `h4-aerial-observation` | “Drone / aerial” is the core requirement, but V1 cannot enforce aerial embodiment. Removing it would erase the case's purpose. | Aerial / embodiment capability attributes consumed by Matching. |
| `i3-relocate-careful-handling` | “Handle gently throughout” is an explicit handling constraint with no structured canonical carrier. | Handling / manipulation semantic constraints. |
| `c2-map-publish-then-infer` | Cross-node machine-dog -> edge-node placement is not enforceable, and `compute.infer.model` lacks user/policy provenance. | Placement / provider-class constraints plus explicit model provenance/default policy. |
| `c3-sequential-handoff` | Visitor registration / reception is an action, not an observation condition, and no canonical interaction operation exists. | Interaction / registration operation family. |
| `t3-exclusive-corridor` | “Go charge” is not equivalent to moving to a charger, and generic `space` lacks a resource identity for “this narrow corridor”. | Charging operation and named/shared resource identity semantics. |
| `t5-mixed-constraints` | “Main elevator occupancy <= 10 min” cannot be represented by `resource {kind, units}`. | Resource identity plus occupancy / lease duration semantics. |
| `m3-patrol-observe` | Conditional “if an object is found then capture/report” requires both conditional Task semantics and capture/report operations. | Conditional Mission execution plus artifact capture/report semantics. |

## Freeze rule

The 20-case `v1-freeze.yaml` is the source of truth for the final Mission
Front-half V1 real-model regression.

The 9 deferred cases are **not failures of that regression** and must not be
silently rewritten into weaker tasks during evaluation. They are retained as
future semantic-surface backlog.

After the final V1 freeze regression, the case set should not be changed in
response to model outcomes. Any later semantic-surface expansion must use a new
versioned suite.
