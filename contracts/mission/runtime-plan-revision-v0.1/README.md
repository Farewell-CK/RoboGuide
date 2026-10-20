# Runtime plan revision v0.1

`command.schema.json` describes the closed Controller command envelope and its eight actions.
Actual TLS leaf, caller role, immutable Mission tenant and current writer authority come from the
transport and committed registries. No request can supply those permissions or a stopped/success flag.

Schemas validate syntax. Published-plan decoding, original goal/Actor/Context compatibility,
actual completed facts, UTF-8 byte budgets, exact current Review, source/owner/session/attempt
coverage and original stop/revoke deadlines are validated by the maintained authority code.
`goal_context` declares original constraints; it is not an observed world fact. MI provenance remains
the authenticated proposer's report until backed by actual MI provider-call records.

See [the application contract](../../../docs/development/runtime-plan-revision-v0.1.md) and
[the independent goal envelope](../goal-evidence-v0.1/README.md). Automatic novel MI work and scoped
Memory transfer remain required; these schemas do not declare overall SoftwareComplete.
