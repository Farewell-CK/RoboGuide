//! Mission-wide planning helpers kept separate from the core domain value definitions.

use crate::{ActorId, CapabilityRequirement, MissionPlan, OperationRef};
use std::collections::BTreeMap;

impl MissionPlan {
    /// Collects each Actor's exact operations without deriving them from capability names.
    ///
    /// This is declared plan evidence only; it neither reserves future availability
    /// nor changes the current-operation checks at Proposal, Commit or Bind.
    pub fn actor_operations(&self) -> BTreeMap<ActorId, Vec<OperationRef>> {
        let mut operations = BTreeMap::new();
        for task in self.task_graph().tasks() {
            for role in task.requirement().roles() {
                if let Some(actor) = role.actor_id() {
                    let entry = operations.entry(actor.clone()).or_insert_with(Vec::new);
                    if let Some(intent) = task.execution_intent(role.role_id())
                        && !entry.contains(intent.operation())
                    {
                        entry.push(intent.operation().clone());
                    }
                }
            }
        }
        operations
    }

    /// Aggregates every actor capability and exact contract requirement in the Task Graph.
    pub fn actor_requirements(&self) -> BTreeMap<ActorId, Vec<CapabilityRequirement>> {
        let mut requirements = BTreeMap::new();
        for task in self.task_graph().tasks() {
            for role in task.requirement().roles() {
                if let Some(actor) = role.actor_id() {
                    let entry = requirements.entry(actor.clone()).or_insert_with(Vec::new);
                    for item in role.capability_requirements().iter() {
                        if !entry.contains(item) {
                            entry.push(item.clone());
                        }
                    }
                }
            }
        }
        requirements
    }
}
