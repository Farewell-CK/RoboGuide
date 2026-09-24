//! Immutable accepted-plan topology carried with physical Execute commands.

use crate::{
    ActorId, ExecutionCouplingMode, ExecutionGroupId, MissionId, MissionPlan, RoleId, TaskId,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

/// Exact schema of deployment session metadata, separate from MissionPlan semantics.
pub const EXECUTION_SESSION_SCHEMA: &str = "roboguide.execution-session/v0.1";

/// One accepted logical execution slot and its declared task prerequisites.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionSessionSlot {
    /// Mission Task identity.
    pub task_id: TaskId,
    /// Task-local Role identity.
    pub role_id: RoleId,
    /// Logical Actor retained across tasks, never a Node selector.
    pub actor_id: ActorId,
    /// Exact DAG prerequisites declared by the accepted plan.
    pub dependencies: Vec<TaskId>,
    /// Whether the accepted Task has no execution-time coordination requirement.
    pub independent: bool,
}

/// Digest-bound whole-Group topology evidence transported to Local EAIOS sessions.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionSessionDescriptor {
    /// Explicit schema marker for downstream strict parsing.
    pub schema_version: String,
    /// Mission owning every slot.
    pub mission_id: MissionId,
    /// Control-created Mission-level Group identity.
    pub group_id: ExecutionGroupId,
    /// All accepted Task/Role slots, ordered by exact identity.
    pub slots: Vec<ExecutionSessionSlot>,
    /// SHA-256 over the canonical JSON object without this field.
    pub digest: String,
}

impl ExecutionSessionDescriptor {
    /// Derives immutable topology from an accepted plan, or leaves pre-v0.8 plans on legacy routing.
    ///
    /// This is planning evidence only: it does not choose a Node, reserve resources, or
    /// alter independent Task semantics. Missing Actor metadata in a v0.8 plan fails.
    pub fn from_plan(
        plan: &MissionPlan,
        group_id: ExecutionGroupId,
    ) -> Result<Option<Self>, String> {
        if plan.schema_version() != crate::MISSION_PLAN_SCHEMA_V0_8 {
            return Ok(None);
        }
        let mut slots = Vec::new();
        for task in plan.task_graph().tasks() {
            let context = plan
                .contexts()
                .iter()
                .find(|context| context.context_id() == task.continuity().context_id())
                .ok_or_else(|| format!("Task {} has no accepted Context", task.task_id()))?;
            let mode = task
                .continuity()
                .coupling_mode_override()
                .unwrap_or_else(|| context.coupling_mode());
            let independent =
                mode == ExecutionCouplingMode::Independent && context.relations().is_empty();
            for role in task.requirement().roles() {
                let actor_id = role.actor_id().ok_or_else(|| {
                    format!(
                        "v0.8 Task {} Role {} has no Actor",
                        task.task_id(),
                        role.role_id()
                    )
                })?;
                let mut dependencies = task.dependencies().to_vec();
                dependencies.sort();
                slots.push(ExecutionSessionSlot {
                    task_id: task.task_id().clone(),
                    role_id: role.role_id().clone(),
                    actor_id: actor_id.clone(),
                    dependencies,
                    independent,
                });
            }
        }
        slots.sort_by(|left, right| {
            (&left.task_id, &left.role_id).cmp(&(&right.task_id, &right.role_id))
        });
        if slots.is_empty()
            || slots
                .iter()
                .map(|slot| (&slot.task_id, &slot.role_id))
                .collect::<BTreeSet<_>>()
                .len()
                != slots.len()
        {
            return Err("execution session contains no slots or duplicate Task/Role slots".into());
        }
        let mut result = Self {
            schema_version: EXECUTION_SESSION_SCHEMA.to_string(),
            mission_id: plan.goal().mission_id().clone(),
            group_id,
            slots,
            digest: String::new(),
        };
        result.digest = result.canonical_digest()?;
        Ok(Some(result))
    }

    /// Recomputes the cross-language digest over sorted-key compact UTF-8 JSON.
    pub fn canonical_digest(&self) -> Result<String, String> {
        let value = serde_json::json!({
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "group_id": self.group_id,
            "slots": self.slots,
        });
        let encoded = serde_json::to_vec(&value).map_err(|error| error.to_string())?;
        Ok(format!("sha256:{:x}", Sha256::digest(encoded)))
    }

    /// Fences tampered or cross-request session evidence before dispatch.
    pub fn validate_slot(
        &self,
        mission_id: &MissionId,
        group_id: &ExecutionGroupId,
        task_id: &TaskId,
        role_id: &RoleId,
    ) -> Result<(), String> {
        let slot_keys = self
            .slots
            .iter()
            .map(|slot| (&slot.task_id, &slot.role_id))
            .collect::<Vec<_>>();
        let task_ids = self
            .slots
            .iter()
            .map(|slot| &slot.task_id)
            .collect::<BTreeSet<_>>();
        let canonical_slots = slot_keys.windows(2).all(|pair| pair[0] < pair[1]);
        let canonical_dependencies = self.slots.iter().all(|slot| {
            slot.dependencies.windows(2).all(|pair| pair[0] < pair[1])
                && slot
                    .dependencies
                    .iter()
                    .all(|dependency| dependency != &slot.task_id && task_ids.contains(dependency))
        });
        if self.schema_version != EXECUTION_SESSION_SCHEMA
            || &self.mission_id != mission_id
            || &self.group_id != group_id
            || self.digest != self.canonical_digest()?
            || self.slots.is_empty()
            || !canonical_slots
            || !canonical_dependencies
            || !self
                .slots
                .iter()
                .any(|slot| &slot.task_id == task_id && &slot.role_id == role_id)
        {
            return Err("execution session identity, slot, or digest is invalid".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a representative accepted-plan descriptor without simulator dependencies.
    fn descriptor() -> ExecutionSessionDescriptor {
        let mut session = ExecutionSessionDescriptor {
            schema_version: EXECUTION_SESSION_SCHEMA.to_string(),
            mission_id: MissionId::new("mission").unwrap(),
            group_id: ExecutionGroupId::new("group").unwrap(),
            slots: vec![
                ExecutionSessionSlot {
                    task_id: TaskId::new("first").unwrap(),
                    role_id: RoleId::new("role").unwrap(),
                    actor_id: ActorId::new("participant").unwrap(),
                    dependencies: vec![],
                    independent: true,
                },
                ExecutionSessionSlot {
                    task_id: TaskId::new("second").unwrap(),
                    role_id: RoleId::new("role").unwrap(),
                    actor_id: ActorId::new("participant").unwrap(),
                    dependencies: vec![TaskId::new("first").unwrap()],
                    independent: true,
                },
            ],
            digest: String::new(),
        };
        session.digest = session.canonical_digest().unwrap();
        session
    }

    /// A legitimate later slot is admitted, while stale and forged topology is refused.
    #[test]
    fn session_slot_validation_checks_identity_digest_and_graph_shape() {
        let session = descriptor();
        assert_eq!(
            session.digest,
            "sha256:7569dee8adc832c5811a78d034ada84e166014586cca5c8689317900a04e7099"
        );
        let mission = MissionId::new("mission").unwrap();
        let group = ExecutionGroupId::new("group").unwrap();
        let task = TaskId::new("second").unwrap();
        let role = RoleId::new("role").unwrap();
        assert!(
            session
                .validate_slot(&mission, &group, &task, &role)
                .is_ok()
        );
        assert!(
            session
                .validate_slot(&MissionId::new("other").unwrap(), &group, &task, &role)
                .is_err()
        );
        let mut changed = session.clone();
        changed.slots[1].actor_id = ActorId::new("other").unwrap();
        assert!(
            changed
                .validate_slot(&mission, &group, &task, &role)
                .is_err()
        );
        changed.digest = changed.canonical_digest().unwrap();
        changed.slots[1].dependencies = vec![TaskId::new("missing").unwrap()];
        changed.digest = changed.canonical_digest().unwrap();
        assert!(
            changed
                .validate_slot(&mission, &group, &task, &role)
                .is_err()
        );
        changed.slots.swap(0, 1);
        changed.digest = changed.canonical_digest().unwrap();
        assert!(
            changed
                .validate_slot(&mission, &group, &task, &role)
                .is_err()
        );
    }
}
