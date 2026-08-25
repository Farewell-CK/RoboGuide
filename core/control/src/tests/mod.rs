//! Deterministic Control Plane behavior tests grouped by responsibility.

include!("support.rs");
include!("reconciliation_pipeline.rs");
include!("recovery_commitment.rs");
include!("group_lifecycle.rs");
include!("matching_coordination.rs");
include!("node_state.rs");
include!("allocation_projection.rs");
include!("actor_continuity.rs");
include!("mission_execution_group.rs");
