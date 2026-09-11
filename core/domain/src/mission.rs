//! Mission goal, planned task, task graph, and accepted plan definitions.

use crate::*;
use std::collections::{BTreeMap, BTreeSet};

/// A user-visible mission objective before global scheduling decisions.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct MissionGoal {
    /// Stable mission identity shared by every task in the graph.
    mission_id: MissionId,
    /// Outcome Mission Intelligence must decompose without selecting nodes.
    objective: String,
}

impl MissionGoal {
    /// Creates a mission goal with a nonblank objective.
    pub fn new(mission_id: MissionId, objective: impl Into<String>) -> Result<Self, DomainError> {
        let objective = objective.into();
        if objective.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "mission objective",
            });
        }
        Ok(Self {
            mission_id,
            objective,
        })
    }

    /// Returns the stable mission identity.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the requested user-visible outcome.
    pub fn objective(&self) -> &str {
        &self.objective
    }
}

/// One Task Graph node with dependencies and role-level execution requirements.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PlannedTask {
    /// Human-readable task outcome used for review and diagnostics.
    description: String,
    /// Task requirements consumed by Control capability matching.
    requirement: TaskRequirement,
    /// Canonical operation intent for each declared role.
    execution_intents: BTreeMap<RoleId, ExecutionIntent>,
    /// Tasks that must complete before this task becomes ready.
    dependencies: Vec<TaskId>,
    /// Context and resource-lifetime declarations supplied by Mission Intelligence.
    continuity: TaskContinuity,
    /// Mission-declared evidence basis for accepting the human-readable Task outcome.
    #[serde(default)]
    satisfaction_basis: TaskSatisfactionBasis,
    /// Explicit semantic effect that must hold before the Task advances the DAG.
    #[serde(default = "legacy_expected_effect")]
    expected_effect: String,
}

impl PlannedTask {
    /// Creates a task while rejecting blank descriptions and duplicate dependencies.
    pub fn new(
        description: impl Into<String>,
        requirement: TaskRequirement,
        execution_intents: BTreeMap<RoleId, ExecutionIntent>,
        dependencies: Vec<TaskId>,
        continuity: TaskContinuity,
    ) -> Result<Self, DomainError> {
        Self::new_with_satisfaction(
            description,
            requirement,
            execution_intents,
            dependencies,
            continuity,
            TaskSatisfactionBasis::ExecutionReport,
        )
    }

    /// Creates a Task with an explicit semantic-satisfaction evidence policy.
    pub fn new_with_satisfaction(
        description: impl Into<String>,
        requirement: TaskRequirement,
        execution_intents: BTreeMap<RoleId, ExecutionIntent>,
        dependencies: Vec<TaskId>,
        continuity: TaskContinuity,
        satisfaction_basis: TaskSatisfactionBasis,
    ) -> Result<Self, DomainError> {
        let description = description.into();
        Self::new_with_completion(
            description.clone(),
            requirement,
            execution_intents,
            dependencies,
            continuity,
            description,
            satisfaction_basis,
        )
    }

    /// Creates a Task with an explicit expected effect and satisfaction evidence policy.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_completion(
        description: impl Into<String>,
        requirement: TaskRequirement,
        execution_intents: BTreeMap<RoleId, ExecutionIntent>,
        dependencies: Vec<TaskId>,
        continuity: TaskContinuity,
        expected_effect: impl Into<String>,
        satisfaction_basis: TaskSatisfactionBasis,
    ) -> Result<Self, DomainError> {
        let description = description.into();
        let expected_effect = expected_effect.into();
        if description.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "task description",
            });
        }
        if expected_effect.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "task expected effect",
            });
        }
        let unique_dependencies: BTreeSet<&TaskId> = dependencies.iter().collect();
        if unique_dependencies.len() != dependencies.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("task {} has duplicate dependencies", requirement.task_id()),
            });
        }
        if dependencies
            .iter()
            .any(|dependency| dependency == requirement.task_id())
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("task {} depends on itself", requirement.task_id()),
            });
        }
        let required_roles = requirement
            .roles()
            .iter()
            .map(RoleRequirement::role_id)
            .collect::<BTreeSet<_>>();
        let intent_roles = execution_intents.keys().collect::<BTreeSet<_>>();
        if required_roles != intent_roles {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "task {} execution intents must exactly cover its roles",
                    requirement.task_id()
                ),
            });
        }
        if continuity
            .context_roles()
            .keys()
            .chain(continuity.resource_scopes().keys())
            .any(|role_id| !required_roles.contains(role_id))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "task {} continuity references an unknown role",
                    requirement.task_id()
                ),
            });
        }
        Ok(Self {
            description,
            requirement,
            execution_intents,
            dependencies,
            continuity,
            satisfaction_basis,
            expected_effect,
        })
    }

    /// Returns the task identity carried by its execution requirements.
    pub fn task_id(&self) -> &TaskId {
        self.requirement.task_id()
    }

    /// Returns the human-readable task outcome.
    pub fn description(&self) -> &str {
        &self.description
    }

    /// Returns the role-level requirements consumed by Control.
    pub const fn requirement(&self) -> &TaskRequirement {
        &self.requirement
    }

    /// Returns the canonical execution intent associated with one role.
    pub fn execution_intent(&self, role_id: &RoleId) -> Option<&ExecutionIntent> {
        self.execution_intents.get(role_id)
    }

    /// Returns all role intents in stable role-identity order.
    pub const fn execution_intents(&self) -> &BTreeMap<RoleId, ExecutionIntent> {
        &self.execution_intents
    }

    /// Returns prerequisite task identities in declaration order.
    pub fn dependencies(&self) -> &[TaskId] {
        &self.dependencies
    }

    /// Returns this Task's semantic continuity and resource-lifetime declaration.
    pub const fn continuity(&self) -> &TaskContinuity {
        &self.continuity
    }

    /// Returns the evidence policy Orchestration applies after local execution completes.
    pub const fn satisfaction_basis(&self) -> &TaskSatisfactionBasis {
        &self.satisfaction_basis
    }

    /// Returns the semantic effect whose evidence is required for Task satisfaction.
    pub fn expected_effect(&self) -> &str {
        &self.expected_effect
    }
}

/// Supplies a compatibility marker when restoring a pre-v0.7 checkpoint.
fn legacy_expected_effect() -> String {
    "legacy Task description defines the expected effect".to_string()
}

/// A validated acyclic Task Graph owned by one mission.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TaskGraph {
    /// Mission that owns every task in this graph.
    mission_id: MissionId,
    /// Tasks retained in planner declaration order.
    tasks: Vec<PlannedTask>,
}

impl TaskGraph {
    /// Creates a graph and rejects identity mismatch, unknown dependencies, or cycles.
    pub fn new(mission_id: MissionId, tasks: Vec<PlannedTask>) -> Result<Self, DomainError> {
        if tasks.is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "task graph must not be empty".to_string(),
            });
        }
        let mut dependencies = BTreeMap::<TaskId, BTreeSet<TaskId>>::new();
        for task in &tasks {
            if task.requirement().mission_id() != &mission_id {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("task {} belongs to another mission", task.task_id()),
                });
            }
            if dependencies
                .insert(
                    task.task_id().clone(),
                    task.dependencies().iter().cloned().collect(),
                )
                .is_some()
            {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("duplicate task id {}", task.task_id()),
                });
            }
        }
        let known_tasks: BTreeSet<TaskId> = dependencies.keys().cloned().collect();
        for (task_id, prerequisites) in &dependencies {
            if let Some(unknown) = prerequisites
                .iter()
                .find(|dependency| !known_tasks.contains(*dependency))
            {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("task {task_id} depends on unknown task {unknown}"),
                });
            }
        }
        let mut remaining = dependencies;
        while !remaining.is_empty() {
            let ready: BTreeSet<TaskId> = remaining
                .iter()
                .filter(|(_, prerequisites)| prerequisites.is_empty())
                .map(|(task_id, _)| task_id.clone())
                .collect();
            if ready.is_empty() {
                return Err(DomainError::InvalidMissionPlan {
                    reason: "task graph contains a cycle".to_string(),
                });
            }
            remaining.retain(|task_id, _| !ready.contains(task_id));
            for prerequisites in remaining.values_mut() {
                prerequisites.retain(|dependency| !ready.contains(dependency));
            }
        }
        Ok(Self { mission_id, tasks })
    }

    /// Returns the mission that owns this graph.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns tasks in planner declaration order.
    pub fn tasks(&self) -> &[PlannedTask] {
        &self.tasks
    }

    /// Returns tasks whose dependencies are all present in the completed set.
    pub fn ready_tasks(&self, completed: &BTreeSet<TaskId>) -> Vec<&PlannedTask> {
        self.tasks
            .iter()
            .filter(|task| {
                !completed.contains(task.task_id())
                    && task
                        .dependencies()
                        .iter()
                        .all(|dependency| completed.contains(dependency))
            })
            .collect()
    }
}

/// A versioned Mission Intelligence result accepted by the DEAIOS core.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MissionPlan {
    /// User-visible goal preserved across planning and recovery.
    goal: MissionGoal,
    /// Mission-scoped logical participants independent of concrete Node placement.
    #[serde(default)]
    actors: Vec<MissionActor>,
    /// Validated task decomposition and execution requirements.
    task_graph: TaskGraph,
    /// Mission Intelligence contexts available to every planned Task.
    contexts: Vec<CoordinationContext>,
}

impl MissionPlan {
    /// Creates a plan only when the goal and Task Graph share one mission identity.
    pub fn new(
        goal: MissionGoal,
        task_graph: TaskGraph,
        contexts: Vec<CoordinationContext>,
    ) -> Result<Self, DomainError> {
        let actors = contexts
            .iter()
            .flat_map(CoordinationContext::roles)
            .map(ContextRole::actor_id)
            .cloned()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(MissionActor::new)
            .collect();
        Self::new_with_actors(goal, actors, task_graph, contexts)
    }

    /// Creates a plan with explicit logical Actors and normalized ContextRole references.
    pub fn new_with_actors(
        goal: MissionGoal,
        actors: Vec<MissionActor>,
        task_graph: TaskGraph,
        contexts: Vec<CoordinationContext>,
    ) -> Result<Self, DomainError> {
        if goal.mission_id() != task_graph.mission_id() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "goal and task graph mission ids differ".to_string(),
            });
        }
        let context_ids = contexts
            .iter()
            .map(CoordinationContext::context_id)
            .collect::<BTreeSet<_>>();
        if context_ids.len() != contexts.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Mission Plan has duplicate context ids".to_string(),
            });
        }
        let actor_ids = actors.iter().map(MissionActor::id).collect::<BTreeSet<_>>();
        if actor_ids.len() != actors.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Mission Plan actors must be unique".to_string(),
            });
        }
        if contexts
            .iter()
            .flat_map(CoordinationContext::roles)
            .any(|role| !actor_ids.contains(role.actor_id()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: "ContextRole references an undeclared Mission Actor".to_string(),
            });
        }
        let relation_ids = contexts
            .iter()
            .flat_map(CoordinationContext::relations)
            .map(ExecutionRelationSpec::relation_id)
            .collect::<BTreeSet<_>>();
        let relation_count = contexts
            .iter()
            .map(|context| context.relations().len())
            .sum::<usize>();
        if relation_ids.len() != relation_count {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Mission Plan has duplicate execution relation ids".to_string(),
            });
        }
        for task in task_graph.tasks() {
            let context = contexts
                .iter()
                .find(|context| context.context_id() == task.continuity().context_id())
                .ok_or_else(|| DomainError::InvalidMissionPlan {
                    reason: format!("task {} references an unknown context", task.task_id()),
                })?;
            for (role_id, context_role_id) in task.continuity().context_roles() {
                let context_role = context.role(context_role_id).ok_or_else(|| {
                    DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} role {role_id} references an unknown context role",
                            task.task_id()
                        ),
                    }
                })?;
                let actor_id = task
                    .requirement()
                    .roles()
                    .iter()
                    .find(|role| role.role_id() == role_id)
                    .and_then(RoleRequirement::actor_id);
                if actor_id != Some(context_role.actor_id()) {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} role {role_id} actor differs from its context role",
                            task.task_id()
                        ),
                    });
                }
            }
            for (role_id, scope) in task.continuity().resource_scopes() {
                if *scope == ResourceBindingScope::Context
                    && task.continuity().context_role(role_id).is_none()
                {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} context-scoped role {role_id} has no ContextRole",
                            task.task_id()
                        ),
                    });
                }
            }
            let coupling_mode = task
                .continuity()
                .coupling_mode_override()
                .unwrap_or_else(|| context.coupling_mode());
            context.validate_mechanisms_for(coupling_mode)?;
        }
        for context in &contexts {
            for relation in context.relations() {
                if relation.kind() != relation.relation_type().kind() {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "execution relation {} has inconsistent kind and typed relation",
                            relation.relation_id()
                        ),
                    });
                }
                validate_relation_endpoint(&task_graph, context, relation.source())?;
                validate_relation_endpoint(&task_graph, context, relation.target())?;
                if relation.source().task_id() != relation.target().task_id()
                    && (task_depends_on(
                        &task_graph,
                        relation.source().task_id(),
                        relation.target().task_id(),
                    ) || task_depends_on(
                        &task_graph,
                        relation.target().task_id(),
                        relation.source().task_id(),
                    ))
                {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "execution relation {} connects Tasks ordered by the DAG",
                            relation.relation_id()
                        ),
                    });
                }
            }
        }
        Ok(Self {
            goal,
            actors,
            task_graph,
            contexts,
        })
    }

    /// Returns the versioned adapter contract represented by this domain shape.
    pub const fn schema_version(&self) -> &'static str {
        MISSION_PLAN_SCHEMA_V0_7
    }

    /// Returns the original mission goal.
    pub const fn goal(&self) -> &MissionGoal {
        &self.goal
    }

    /// Returns logical Mission Actors in stable declaration order.
    pub fn actors(&self) -> &[MissionActor] {
        &self.actors
    }

    /// Returns the validated Task Graph.
    pub const fn task_graph(&self) -> &TaskGraph {
        &self.task_graph
    }

    /// Returns Mission Intelligence contexts in declaration order.
    pub fn contexts(&self) -> &[CoordinationContext] {
        &self.contexts
    }
}

/// Confirms one relation endpoint is an exact Task/Role in the containing Context.
fn validate_relation_endpoint(
    graph: &TaskGraph,
    context: &CoordinationContext,
    endpoint: &PlannedExecutionRef,
) -> Result<(), DomainError> {
    let task = graph
        .tasks()
        .iter()
        .find(|task| task.task_id() == endpoint.task_id())
        .ok_or_else(|| DomainError::InvalidMissionPlan {
            reason: format!(
                "execution relation references unknown Task {}",
                endpoint.task_id()
            ),
        })?;
    if task.continuity().context_id() != context.context_id() {
        return Err(DomainError::InvalidMissionPlan {
            reason: format!(
                "execution relation endpoint {}:{} belongs to another Context",
                endpoint.task_id(),
                endpoint.role_id()
            ),
        });
    }
    if !task
        .requirement()
        .roles()
        .iter()
        .any(|role| role.role_id() == endpoint.role_id())
    {
        return Err(DomainError::InvalidMissionPlan {
            reason: format!(
                "execution relation references unknown Role {} in Task {}",
                endpoint.role_id(),
                endpoint.task_id()
            ),
        });
    }
    Ok(())
}

/// Returns whether `task_id` transitively depends on `candidate_dependency`.
fn task_depends_on(graph: &TaskGraph, task_id: &TaskId, candidate_dependency: &TaskId) -> bool {
    let Some(task) = graph.tasks().iter().find(|task| task.task_id() == task_id) else {
        return false;
    };
    task.dependencies().iter().any(|dependency| {
        dependency == candidate_dependency
            || task_depends_on(graph, dependency, candidate_dependency)
    })
}
