# Active State hazard gates v0.3

An optional `active_stop_timeout_ms` explicitly opts a Readiness gate into ongoing monitoring.
Each original attempt retains its immutable gate revision. Current source/session/freshness and
conflict checks apply throughout execution; revocation or policy expansion requires stopping,
not silent continuation. Local EAIOS owns safe-state observations. See
[ADR-0210](../../../docs/decisions/0210-reviewed-execution-coordination.md).
