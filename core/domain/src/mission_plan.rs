//! Mission-wide planning helpers kept separate from the core domain value definitions.

use crate::{ActorId, CapabilityContractRef, CapabilityKind, MissionPlan};
use std::collections::BTreeMap;

impl MissionPlan {
    /// Aggregates every actor capability and exact contract requirement in the Task Graph.
    pub fn actor_requirements(
        &self,
    ) -> BTreeMap<ActorId, Vec<(CapabilityKind, CapabilityContractRef)>> {
        let mut requirements = BTreeMap::new();
        for task in self.task_graph().tasks() {
            for role in task.requirement().roles() {
                if let (Some(actor), Some(contract)) = (role.actor_id(), role.required_contract()) {
                    let entry = requirements.entry(actor.clone()).or_insert_with(Vec::new);
                    let item = (role.capability(), contract.clone());
                    if !entry.contains(&item) {
                        entry.push(item);
                    }
                }
            }
        }
        requirements
    }
}
