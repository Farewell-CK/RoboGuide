# ADR-0227: Explicit EMOS target-identity compatibility source view

Status: Implemented; deterministic and physical acceptance are recorded separately.

## Context

The published EMOS HSSD scene-description function resolves a task alias, then an
independent fallback overwrites it with the original object handle. The task and
tool refer to the alias while geometry refers to another name. This regression
was introduced by upstream `c70286ea470dce825bcf27aae7a4b17e9f2a2811` and is present
in the selected public reference `e9501db45d634b087bf5d1a14228266685e8feeb`.
An upstream release is not proof of the paper's exact execution version.

Deleting the fallback wholesale would revive the older unbound or inherited name
for ordinary background objects. The repair must preserve both explicit task
identity and the existing fallback for objects without a task alias or label.

## Decision

Deployment preparation owns `habitat_local_eaios.vendor_compatibility`. Its explicit
CLI creates a new private vendor source view; no runtime automatically applies it.
The named function must match the known published function text digest. The only
transformation joins the final unnamed-object `if` to the existing `if/elif` chain.
Authoritative alias takes precedence over a semantic label, which takes precedence
over the original unnamed-object or handle fallback. Geometry and all other module
bytes remain unchanged. Unknown, ambiguous, decorated or already patched functions
refuse before output creation.

The command copies only the three declared source packages, preserves file modes,
excludes interpreter caches and environment files, and never edits the supplied
tree. Shared dataset references receive no preparation writes. Actual runs still
need the existing run-private writable-asset policy. Source and copied inventories
are checked before and after the single change. Existing destinations, outputs
inside the source, package symlinks, copy errors and source drift cannot produce a
complete receipt. Incomplete private output is retained for inspection.

`roboguide.vendor-compatibility/v0.1` records the explicit
`emos-hssd-target-identity/v0.1` profile, exact original and prepared package bytes,
named-function digests and the one changed path. Its upstream reference qualifies
that function only; existing deployment changes in other files are not relabelled
as pristine upstream code. The generated source view and receipt remain local
experiment/deployment artifacts, never source-control results.

Both compared systems use private copies of the same prepared vendor source.
The existing evaluation source/module gates freeze its actual bytes and receipt;
the compatibility identity must be included in the frozen backend identity. Native
EMOS retains its planner, reflection, tools, skills and feedback. RoboGuide retains
its own disclosed planning, guard and feedback. No historical input/result is
rewritten, and the new baseline is not called an exact paper reproduction.

## Boundaries and acceptance

This changes scene input identity, not object positions, robot initialization,
physics, predicates, thresholds, model, skill/world budgets or success authority.
It creates zero models, simulators, Nodes, Missions and workers. It does not fix the
original EMOS local-feedback design or promise a better success rate.

Tests exercise the actual published function fixture, alias and label precedence,
ordinary background and unnamed fallback, unchanged non-target source, both arm
copies, private runtime assets, unknown source, unsafe ownership, copy errors and
concurrent source/copy drift. The actual Habitat Python interpreter additionally
prepares and verifies a real source view without importing EMOS or Habitat.
Physical validation requires a new frozen manifest and fixed small samples; those
samples do not estimate a population success rate. A full batch waits for human
review and separately records success, failure, unavailable evidence and retries.

Baseline acceptance also needs actual complete reset orientation. Physical diagnostics
v0.8 reads the existing world-from-base Matrix4 once at reset and terminal boundaries,
with explicit row-major storage. It does not add per-step matrix reads or change pose.
The vendor scalar is labelled as a scalar, not inferred signed yaw. New reset comparison
requires valid complete matrix evidence on both arms; missing data cannot fall back to
the scalar. Legacy archives retain their old comparison meaning and are explicitly
qualified as scalar-only. Optional read failures remain unavailable and cannot affect
the original execution or final diagnostic flush. Official evaluation remains separate.
