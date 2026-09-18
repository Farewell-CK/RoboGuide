# Mission Grounding Context v0.2

`roboguide.grounding-context/v0.2` adds one optional-to-acquire but explicit
`semantic_evidence` field. The evidence is benchmark-neutral, immutable, and
digest-bound. It carries a joint terminal-state objective and relevant world
semantic context; it does not carry Node inventory, resources, reservations,
Actor placement, or Physical Entity assignments.

Older v0.1 snapshots remain readable as historical records. A v0.2 snapshot
with a missing semantic source retains an explicit acquisition gap and never
silently falls back to a static MissionPlan.
