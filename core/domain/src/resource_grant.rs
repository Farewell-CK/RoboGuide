//! Versioned quantitative execution grants supplied by Control, outside semantic intent.

use sha2::{Digest, Sha256};

use crate::{AllocationOwner, ExecutionGroupId, NodeId, ResourceId, RoleId, TaskRef};

/// Exact original logical capacity authority for one current Task/Role borrower.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceAllocationGrant {
    /// Explicit interpretation; older Node contracts cannot carry these bytes.
    pub schema: String,
    /// Original Control allocation identity, independent of caller strings or device naming.
    pub allocation_id: String,
    /// Original monotonic allocation generation, never reset by release or restart.
    pub generation: u64,
    /// Actual advertised resource, not a generated virtual resource.
    pub resource_id: ResourceId,
    /// Exact original semantic unit interpretation; unlike units it cannot be inferred from capacity.
    pub resource_class: String,
    /// Registered Node that owns the declared pool.
    pub node_id: NodeId,
    /// Exact admitted Group; source and replacement Groups cannot share a grant.
    pub group_id: ExecutionGroupId,
    /// Original Task or ContextRole owner of the parent allocation.
    pub owner: AllocationOwner,
    /// Immutable accepted Mission tenant.
    pub tenant_id: String,
    /// Task currently borrowing the original capacity.
    pub task_ref: TaskRef,
    /// Logical Role currently borrowing it.
    pub role_id: RoleId,
    /// Exact positive units of this borrower.
    pub units: u32,
    /// Original parent allocation limit; simultaneous borrowers cannot exceed it.
    pub allocation_units: u32,
    /// Original registered capacity observed at admission, not a current hardware fact.
    pub admitted_capacity: u32,
}

impl ResourceAllocationGrant {
    /// Hashes the original typed parent identity; Context borrower changes cannot create another owner.
    pub fn original_allocation_id(&self) -> String {
        let role = matches!(self.owner, AllocationOwner::Task(_)).then_some(&self.role_id);
        let canonical = serde_json::to_vec(&serde_json::json!([
            self.schema,
            self.generation,
            self.resource_id,
            self.resource_class,
            self.node_id,
            self.group_id,
            self.owner,
            role,
            self.tenant_id,
            self.allocation_units,
            self.admitted_capacity
        ]))
        .expect("typed allocation identity serializes");
        format!("allocation:sha256:{:x}", Sha256::digest(canonical))
    }

    /// Validates closed positive bounds and original Mission attribution without granting execution.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.resource-allocation-grant/v0.1"
            || self.generation == 0
            || self.units == 0
            || self.units > self.allocation_units
            || self.allocation_units > self.admitted_capacity
            || self.resource_class.trim().is_empty()
            || self.resource_class.len() > 128
            || self.tenant_id.trim().is_empty()
            || self.tenant_id.len() > 128
            || !self
                .allocation_id
                .strip_prefix("allocation:sha256:")
                .is_some_and(|hex| {
                    hex.len() == 64
                        && hex
                            .bytes()
                            .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
                })
            || self.allocation_id != self.original_allocation_id()
            || match &self.owner {
                AllocationOwner::Task(task) => task != &self.task_ref,
                AllocationOwner::Context { mission_id, .. } => {
                    mission_id != self.task_ref.mission_id()
                }
            }
        {
            return Err("resource allocation grant identity, scope or units are invalid".into());
        }
        Ok(())
    }
}
