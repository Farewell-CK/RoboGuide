//! Mission-wide planning helpers kept separate from the core domain value definitions.

use crate::{ActorId, CapabilityRequirement, MissionPlan};
use std::collections::BTreeMap;

impl MissionPlan {
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
