# Mission Grounding Context v0.1

`roboguide.grounding-context/v0.1` freezes the bounded, attributed evidence used by one Mission
Intelligence deliberation cycle.

The current selection policy admits only source-aware World State records whose payload schema is
explicitly approved by Mission Service deployment configuration, plus metadata for Global
Semantic, Experience, and Spatial Memory revisions. The schema allowlist fails closed and its
stable digest is part of `selection_policy_ref`; World object classification alone is not a
visibility grant. It excludes Node health/liveness,
capability/resource inventory, leases, reservations, scheduling calendars, execution attempts,
and other live deployment facts. Memory entries are explicitly `MetadataOnly`; an Artifact
reference does not mean that Mission Intelligence fetched or interpreted its content.

`freshness` is copied from the Controller State query because State receive time and Mission
Service wall time are not assumed to share a clock domain. Acquisition failures are represented
as `gaps`, so an empty or partial context is valid and inspectable. The snapshot digest binds the
request, input dialogue revision, selection policy, evidence, and gaps supplied to Interpreter,
Planner, Reviewer, and Repairer.

Nested JSON is exposed through defensive copies, and restored evidence/gap arrays must retain
canonical order. The fixtures in `fixtures/` are checked by both the Rust producer facade and the
Python Mission consumer so wire-shape drift fails offline tests.
