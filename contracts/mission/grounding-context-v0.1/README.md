# Mission Grounding Context v0.1

`roboguide.grounding-context/v0.1` freezes the bounded, attributed evidence used by one Mission
Intelligence deliberation cycle.

The first selection policy admits source-aware World State records and metadata for Global
Semantic, Experience, and Spatial Memory revisions. It excludes Node health/liveness,
capability/resource inventory, leases, reservations, scheduling calendars, execution attempts,
and other live deployment facts. Memory entries are explicitly `MetadataOnly`; an Artifact
reference does not mean that Mission Intelligence fetched or interpreted its content.

`freshness` is copied from the Controller State query because State receive time and Mission
Service wall time are not assumed to share a clock domain. Acquisition failures are represented
as `gaps`, so an empty or partial context is valid and inspectable. The snapshot digest binds the
request, input dialogue revision, selection policy, evidence, and gaps supplied to Interpreter,
Planner, Reviewer, and Repairer.
