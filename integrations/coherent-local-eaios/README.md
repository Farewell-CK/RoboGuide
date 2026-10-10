# COHERENT Local EAIOS (E2-S0)

This deployment adapter exposes the already-validated COHERENT
`Merom_1_int_Task1` physical plan through RoboGuide's generic HTTP workflow.
It preserves the canonical invocation, creates one durable local handle, runs
the fixed container-side task runner, and reports `COMPLETED` only when both the
COHERENT summary and physical goal checker pass.

This is a minimal control-path smoke. The Local EAIOS operation owns the complete
pre-authored Dog/Drone/Arm plan, so this stage does **not** establish per-robot
RoboGuide allocation or scheduling. The next E2 stage must expose independently
addressable robot operations and represent the handoffs as MissionPlan tasks.

Cancellation is explicitly unsupported in E2-S0 and returns `accepted: false`.
