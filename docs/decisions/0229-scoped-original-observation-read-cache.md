# ADR-0229: Scoped original observation read reuse

Status: Implemented; software, physical equivalence and workload outcomes are separate gates.

## Context

The deployed original `GlobalPredicatesSensor` repeatedly obtains the complete raw
camera dictionary during one predicate-vector calculation. A zero-Provider profile
attributes most step time to repeated rendering. A long run is not, by itself,
evidence of a model-service failure or a deadlocked policy.

## Decision and ownership

`habitat_local_eaios.observation_read_cache` owns an explicitly opted-in shared
backend optimization, `same-read-scope-render-cache/v0.1`. It is off by default.
The native workload constructor and production Stage2 environment constructor
install the same implementation before the original reset. Neither vendor source
nor MI, Control, Node, Local How policy, official predicates or success thresholds
change. The existing controlled-arm lifecycle policies remain separately declared.

Only exact known original predicate source hashes and the original predicate
sensor class admit reuse. During one original `get_observation` callback, an
actual world witness checks simulation time, rigid/articulated body transforms,
joint positions and camera transforms/projections. Every camera must use the
actual original `NoSensorNoiseModel`; device buffers refuse. A changed, unknown
or unsupported witness retains original fresh reads. No action, physics step,
sensor query or RNG sample is added to obtain the witness.

The retained value is an owned copy of raw pixels, never a predicate result.
All predicates and transformations still run. Each reader receives its own copy;
mutation by a consumer cannot affect later readers. Limits are 512 world objects,
64 sensor entries and 64 MiB retained raw buffers. Scope exit, including original
exceptions, removes the private reader hook and discards its sample. Detachment
does not remove a later instrumentation owner's hook. Original renderer and close
errors retain their original meaning; optimization/evidence failures do not
manufacture observations or success.

## Deployment and evidence

`e1-pair` accepts an optional boolean worker configuration field
`observation_read_cache`, default false. It clears inherited Habitat flags and
sets `ROBOGUIDE_HABITAT_OBSERVATION_READ_CACHE=1` only for explicit true, identically
on both arms. Freeze that setting, declaration and implementation digest in the
new population/config/source manifest; do not hot-update a frozen experiment or
combine its outcomes with an older version's denominator.

Both actual runtime source manifests include the loaded implementation.
`observation-read-cache.json` records admission status and final fresh/hit/fallback
counters at environment close. Missing or unsupported optimization remains original
execution, not improved-performance evidence. A queue requiring this optimization
must refuse further dispatch if actual admission or final counters are unavailable.

## Validation and limits

Deterministic tests cover scope separation, independently recomputed truth, owned
buffer mutation, body/joint/clock/camera/noise changes, unsupported buffers/budgets,
original errors, default-off behavior and final close evidence. Real Habitat Python
probes compare all sensor-array bytes, rewards, termination flags and info under
identical original reset and action sequences, including movement. These probes
use no Provider and are not E1 results.

An unchanged short probe does not prove every future trajectory is identical or
promise universal throughput. Paired physical acceptance separately checks actual
loaded sources, full reset transforms, cache admission and complete archival. Normal
official false remains a valid outcome. The optimization does not extend original
world, skill, Provider or invocation budgets and grants no new physical authority.
