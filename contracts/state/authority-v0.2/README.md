# Decision policy registry v0.2

Extends [v0.1](../authority-v0.1/README.md) with explicit planning-only cost source admission.
`decision-policy-command.schema.json` describes the unchanged authenticated revisioned command
endpoint `/v1/state-policies` selecting the new registry interpretation. `scheduling-metrics.schema.json`
keeps missing source measurements nullable. Runtime additionally verifies source/current-session,
Mission tenant, unique enabled candidate, metric uniqueness/aggregate overflow and retained history.
See [ADR-0205](../../../docs/decisions/0205-current-state-scheduling-costs.md).
