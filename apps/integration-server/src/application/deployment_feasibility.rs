//! Deployment-owned reset-state eligibility before Control commits a Mission.

#[cfg(test)]
use super::deployment_document::content_digest;
use super::deployment_operation_admission::DeploymentOperationAdmission;
use super::plan_validation::{MAX_PLAN_ISSUES, PlanIssue};
use crate::*;
#[cfg(test)]
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

/// Fixed, trusted startup observation; no live Node inventory or binding authority.
#[derive(Debug, Clone)]
pub(crate) struct DeploymentFeasibility {
    /// Content identity persisted with each Control candidate restriction.
    digest: String,
    /// Exact canonical operation and destination to observed Node status.
    pub(super) entries: BTreeMap<(String, String), BTreeMap<domain::NodeId, String>>,
    /// Reset identity retained only for validating the optional cost source.
    pub(super) identity: serde_json::Value,
    /// Exact starts retained for cross-source snapshot validation.
    pub(super) initial_positions: serde_json::Value,
    /// Deployment endpoint ownership used to reject mismatched witness records.
    pub(super) node_agents: BTreeMap<domain::NodeId, i64>,
    /// Optional startup ordering, separate from durable negative restrictions.
    initial_preferences: Option<super::initial_operation_preferences::InitialOperationPreferences>,
    /// Explicit opt-in to read-only, initial-world support assessment.
    assessment_enabled: bool,
    /// Optional versioned exact-operation sources, independent of navigation decisions.
    operation_admission: Option<DeploymentOperationAdmission>,
    /// Opt-in live endpoint support; never a reservation or physical binding.
    live_operations: Option<BTreeMap<domain::NodeId, BTreeSet<String>>>,
    /// Producer-owned final verdict barrier; no change to Mission semantics or Control release.
    terminal_verifier_barrier: bool,
    /// Source identity for the public, inventory-free endpoint lifetime declaration.
    registry_digest: Option<String>,
}

/// Check whether concurrent Roles of one Task can use distinct configured endpoints.
///
/// The matching is deployment evidence only. Control still owns the actual
/// proposal, reservation and commit, while this guard prevents a live shared
/// world from accepting a topology that its capacity-one endpoints cannot run.
fn has_distinct_endpoint_assignment(
    actors: &[domain::ActorId],
    candidates: &BTreeMap<domain::ActorId, BTreeSet<domain::NodeId>>,
) -> bool {
    let mut ordered = actors.to_vec();
    ordered.sort_by_key(|actor| candidates.get(actor).map_or(usize::MAX, BTreeSet::len));

    /// Try bounded endpoint combinations without selecting or reserving any Node.
    fn search(
        index: usize,
        actors: &[domain::ActorId],
        candidates: &BTreeMap<domain::ActorId, BTreeSet<domain::NodeId>>,
        used: &mut BTreeSet<domain::NodeId>,
    ) -> bool {
        if index == actors.len() {
            return true;
        }
        let Some(nodes) = candidates.get(&actors[index]) else {
            return false;
        };
        for node in nodes {
            if used.insert(node.clone()) {
                if search(index + 1, actors, candidates, used) {
                    return true;
                }
                used.remove(node);
            }
        }
        false
    }

    search(0, &ordered, candidates, &mut BTreeSet::new())
}

impl DeploymentFeasibility {
    /// Admits validated source evidence while retaining disabled preferences and assessment defaults.
    pub(crate) fn load(path: &Path) -> Result<Self, String> {
        let snapshot = super::deployment_sources::FrozenFeasibilitySnapshot::load(path)?;
        Ok(Self {
            digest: snapshot.digest,
            entries: snapshot.entries,
            identity: snapshot.identity,
            initial_positions: snapshot.initial_positions,
            node_agents: snapshot.node_agents,
            initial_preferences: None,
            assessment_enabled: false,
            operation_admission: snapshot.operation_admission,
            live_operations: snapshot.live_operations,
            terminal_verifier_barrier: snapshot.terminal_verifier_barrier,
            registry_digest: snapshot.registry_digest,
        })
    }

    /// Projects only explicit finality and capacity declarations, never current Node facts.
    pub(crate) fn published_lifetime(&self) -> Option<serde_json::Value> {
        self.terminal_verifier_barrier.then(|| {
            serde_json::json!({
                "declaration_digest": self.registry_digest,
                "configured_endpoint_count": self.node_agents.len(),
                "endpoint_capacity": 1,
                "exclusive_resource_kind": "space",
                "max_slots": 32,
                "resource_release": "after-task-satisfaction",
                "verifier_publication": "after-all-session-slots",
            })
        })
    }

    /// Publishes implemented live topology only, without candidate or physical identities.
    pub(crate) fn published_topology(&self) -> Option<serde_json::Value> {
        self.live_operations.as_ref().map(|_| {
            serde_json::json!({
                "supported_coupling_modes": ["independent"],
                "supports_task_dependencies": true,
                "supports_context_relations": false,
                "actor_binding": "session-stable",
            })
        })
    }

    /// Explains unsupported effective modes and relations at the original logical paths.
    fn coordination_issues(&self, plan: &domain::MissionPlan) -> Vec<PlanIssue> {
        let mut issues = Vec::new();
        let mut seen = BTreeSet::new();
        for (index, task) in plan.task_graph().tasks().iter().enumerate() {
            let Some((context_index, context)) = plan
                .contexts()
                .iter()
                .enumerate()
                .find(|(_, context)| context.context_id() == task.continuity().context_id())
            else {
                continue;
            };
            let mode = task
                .continuity()
                .coupling_mode_override()
                .unwrap_or_else(|| context.coupling_mode());
            if mode != domain::ExecutionCouplingMode::Independent {
                let path = if task.continuity().coupling_mode_override().is_some() {
                    format!("/tasks/{index}/coupling_mode")
                } else {
                    format!("/contexts/{context_index}/coupling_mode")
                };
                if seen.insert(("mode", path.clone())) {
                    issues.push(PlanIssue::new(
                        "deployment.coupling_mode_unsupported",
                        path,
                        "Effective coupling mode is outside the supported modes [independent]. Task dependencies are separate from execution cooperation; preserve real cooperation requirements when reassessing the mode.",
                    ));
                }
            }
            if !context.relations().is_empty() {
                let path = format!("/contexts/{context_index}/relations");
                if seen.insert(("relations", path.clone())) {
                    issues.push(PlanIssue::new(
                        "deployment.context_relations_unsupported",
                        path,
                        "The deployment does not support Context execution relations. Preserve required relations and expose the deployment gap; do not delete real cooperation to pass admission.",
                    ));
                }
            }
            if issues.len() > MAX_PLAN_ISSUES {
                break;
            }
        }
        issues
    }

    /// Reuses submission checks while retaining independently identifiable contract conflicts.
    pub(crate) fn plan_issues(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
    ) -> Vec<PlanIssue> {
        let mut issues = self.coordination_issues(plan);
        if let Err(issue) = self.candidate_restrictions(plan, group_id) {
            issues.push(issue);
        }
        issues
    }

    /// Preserves the actual snapshot identity without copying Node/agent bindings into MI.
    pub(crate) fn planning_identity(&self) -> Option<serde_json::Value> {
        let fields = [
            "run_id",
            "episode_id",
            "scene_id",
            "dataset_revision",
            "dataset_sha256",
            "semantic_evidence_digest",
        ];
        fields
            .iter()
            .all(|field| {
                self.identity[field]
                    .as_str()
                    .is_some_and(|value| !value.trim().is_empty())
            })
            .then(|| {
                serde_json::Value::Object(
                    fields
                        .into_iter()
                        .map(|field| (field.to_owned(), self.identity[field].clone()))
                        .collect(),
                )
            })
    }

    /// Adds a separately configured initial source without renewing it on restore.
    pub(crate) fn configure_initial_preferences(
        &mut self,
        path: &Path,
        source_path: &Path,
        received_at: domain::TimestampMs,
        fresh_controller: bool,
    ) -> Result<(), String> {
        self.initial_preferences = Some(
            super::initial_operation_preferences::InitialOperationPreferences::load(
                path,
                source_path,
                self,
                received_at,
                fresh_controller,
            )?,
        );
        Ok(())
    }

    /// Computes optional initial ordering within the current Control candidates.
    pub(crate) fn initial_preferences_for_plan<
        S: ports::SharedNodeStateReader,
        E: ports::EventSink,
    >(
        &self,
        plan: &domain::MissionPlan,
        control: &control::ControlPlane,
        state: &S,
        now: domain::TimestampMs,
        correlation: &domain::CorrelationId,
        events: &mut E,
    ) -> Result<Option<control::InitialCandidatePreferences>, String> {
        self.initial_preferences
            .as_ref()
            .map(|source| source.for_plan(plan, control, state, now, correlation, events))
            .transpose()
            .map(Option::flatten)
    }

    /// Enables advisory preflight only with an attributed complete-geometry source.
    pub(crate) fn configure_initial_assessment(&mut self) -> Result<(), String> {
        self.initial_preferences
            .as_ref()
            .ok_or("initial support assessment requires initial operation preferences")?
            .validate_assessment_source()?;
        self.assessment_enabled = true;
        Ok(())
    }

    /// Assesses the supplied plan without submitting it or persisting candidate facts.
    pub(crate) fn assess_initial_plan<S: ports::SharedNodeStateReader>(
        &self,
        plan: &domain::MissionPlan,
        control: &control::ControlPlane,
        state: &S,
        now: domain::TimestampMs,
        pristine: bool,
        request_body: &[u8],
    ) -> serde_json::Value {
        super::initial_operation_assessment::assess(
            self,
            self.initial_preferences
                .as_ref()
                .filter(|_| self.assessment_enabled),
            plan,
            control,
            state,
            now,
            pristine,
            request_body,
        )
    }

    /// Derives candidate sets from exact accepted intents without choosing an assignment.
    pub(crate) fn restrictions_for_plan(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
    ) -> Result<Vec<(domain::ActorId, BTreeSet<domain::NodeId>)>, String> {
        if let Some(issue) = self.coordination_issues(plan).into_iter().next() {
            return Err(issue.message);
        }
        self.candidate_restrictions(plan, group_id)
            .map_err(|issue| issue.message)
    }

    /// Computes the original frozen candidate bounds; errors grant no placement authority.
    fn candidate_restrictions(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
    ) -> Result<Vec<(domain::ActorId, BTreeSet<domain::NodeId>)>, PlanIssue> {
        let session = domain::ExecutionSessionDescriptor::from_plan(plan, group_id.clone())?
            .ok_or("deployment feasibility requires MissionPlan v0.8 session metadata")?;
        let mut candidates = BTreeMap::<domain::ActorId, BTreeSet<domain::NodeId>>::new();
        let mut relocation_actors = BTreeMap::<String, BTreeSet<domain::ActorId>>::new();
        for (task_index, task) in plan.task_graph().tasks().iter().enumerate() {
            for (role_index, role) in task.requirement().roles().iter().enumerate() {
                let role_path = format!("/tasks/{task_index}/roles/{role_index}");
                if !role.resource_requirements().iter().any(|resource| {
                    resource.kind() == domain::ResourceKind::Space && resource.units() == 1
                }) {
                    return Err(PlanIssue::new(
                        "deployment.resource_requirement_invalid",
                        format!("{role_path}/requirements/resources"),
                        "shared-world deployment requires one exclusive space:1 slot per Task role",
                    ));
                }
                let actor = role
                    .actor_id()
                    .ok_or("deployment role has no logical Actor")?;
                let intent = task
                    .execution_intent(role.role_id())
                    .ok_or("deployment role lacks canonical intent")?;
                if matches!(
                    intent.operation().to_string().as_str(),
                    "observation.verify@v1" | "observation.acquire@v1"
                ) {
                    let profiles = self.live_operations.as_ref().ok_or_else(|| {
                        PlanIssue::new(
                            "deployment.operation_unsupported",
                            format!("{role_path}/execution_intent/operation"),
                            "observation requires explicit live deployment support",
                        )
                    })?;
                    let condition = match (
                        intent.parameters().len(),
                        intent.parameters().get("expected"),
                    ) {
                        (1, Some(domain::ExecutionValue::String(value))) => value,
                        _ => {
                            return Err(PlanIssue::new(
                                "deployment.intent_invalid",
                                format!("{role_path}/execution_intent/parameters"),
                                "observation requires one exact expected condition",
                            ));
                        }
                    };
                    let entity = condition
                        .strip_prefix("detected(")
                        .and_then(|value| value.strip_suffix(')'))
                        .filter(|value| {
                            !value.trim().is_empty()
                                && !value.contains(['(', ')', '\n', '\r'])
                                && value.len() <= 502
                        })
                        .ok_or_else(|| {
                            PlanIssue::new(
                                "deployment.intent_invalid",
                                format!("{role_path}/execution_intent/parameters/expected"),
                                "observation condition is outside the local read-only profile",
                            )
                        })?;
                    if !self
                        .entries
                        .keys()
                        .any(|(_, destination)| destination == entity)
                    {
                        return Err(PlanIssue::new(
                            "deployment.intent_invalid",
                            format!("{role_path}/execution_intent/parameters/expected"),
                            "observation entity is not in the frozen world",
                        ));
                    }
                    let allowed = profiles
                        .iter()
                        .filter(|(_, operations)| {
                            operations.contains(&intent.operation().to_string())
                        })
                        .map(|(node, _)| node.clone())
                        .collect::<BTreeSet<_>>();
                    if allowed.is_empty() {
                        return Err(PlanIssue::new(
                            "deployment.operation_unsupported",
                            format!("{role_path}/execution_intent/operation"),
                            "No configured live endpoint supports this exact observation operation",
                        ));
                    }
                    candidates
                        .entry(actor.clone())
                        .and_modify(|existing| existing.retain(|node| allowed.contains(node)))
                        .or_insert(allowed);
                    continue;
                }
                if intent.operation().to_string() == "object.relocate@v1" {
                    let admitted = self.operation_admission.as_ref().ok_or_else(|| {
                        PlanIssue::new(
                            "deployment.operation_unsupported",
                            format!("{role_path}/execution_intent/operation"),
                            "relocation requires reset-bound deployment operation admission",
                        )
                    })?;
                    let mut allowed = admitted.candidates(intent).map_err(|message| {
                        PlanIssue::new(
                            "deployment.intent_invalid",
                            format!("{role_path}/execution_intent/parameters"),
                            message,
                        )
                    })?;
                    if let Some(profiles) = &self.live_operations {
                        allowed.retain(|node| {
                            profiles
                                .get(node)
                                .is_some_and(|operations| operations.contains("object.relocate@v1"))
                        });
                    }
                    let Some(domain::ExecutionValue::String(object)) =
                        intent.parameters().get("object")
                    else {
                        unreachable!("operation admission checked the exact parameter contract")
                    };
                    relocation_actors
                        .entry(object.clone())
                        .or_default()
                        .insert(actor.clone());
                    candidates
                        .entry(actor.clone())
                        .and_modify(|existing| existing.retain(|node| allowed.contains(node)))
                        .or_insert(allowed);
                    continue;
                }
                let destination = match (
                    intent.parameters().len(),
                    intent.parameters().get("destination"),
                ) {
                    (1, Some(domain::ExecutionValue::String(value))) if !value.is_empty() => value,
                    _ => {
                        return Err(PlanIssue::new(
                            "deployment.intent_invalid",
                            format!("{role_path}/execution_intent/parameters"),
                            "deployment feasibility requires one exact destination parameter",
                        ));
                    }
                };
                let statuses = self
                    .entries
                    .get(&(intent.operation().to_string(), destination.clone()))
                    .ok_or_else(|| {
                        PlanIssue::new(
                            "deployment.intent_invalid",
                            format!("{role_path}/execution_intent"),
                            "deployment feasibility has no record for an accepted intent",
                        )
                    })?;
                let allowed = statuses
                    .iter()
                    .filter(|(node, status)| {
                        status.as_str() != "incompatible"
                            && self.live_operations.as_ref().is_none_or(|profiles| {
                                profiles.get(*node).is_some_and(|operations| {
                                    operations.contains(&intent.operation().to_string())
                                })
                            })
                    })
                    .map(|(node, _)| node.clone())
                    .collect::<BTreeSet<_>>();
                candidates
                    .entry(actor.clone())
                    .and_modify(|existing| existing.retain(|node| allowed.contains(node)))
                    .or_insert(allowed);
            }
        }
        if candidates.values().any(BTreeSet::is_empty) {
            return Err(PlanIssue::new(
                "deployment.actor_placement_unavailable",
                "/mission/actors",
                "deployment feasibility leaves a logical Actor without a candidate",
            ));
        }
        if relocation_actors.values().any(|actors| actors.len() > 1) {
            return Err(PlanIssue::new(
                "deployment.object_relocation_conflict",
                "/tasks",
                "shared-world deployment does not support concurrent relocation of one object",
            ));
        }
        if self.live_operations.is_some() {
            let mut actors_by_task = BTreeMap::<domain::TaskId, Vec<domain::ActorId>>::new();
            for slot in &session.slots {
                let actors = actors_by_task.entry(slot.task_id.clone()).or_default();
                if actors.contains(&slot.actor_id) {
                    return Err(PlanIssue::new(
                        "deployment.concurrent_actor_reuse",
                        "/tasks",
                        "live shared-world Task roles must use distinct logical Actors",
                    ));
                }
                actors.push(slot.actor_id.clone());
            }
            if actors_by_task.values().any(|actors| {
                actors.len() > 1 && !has_distinct_endpoint_assignment(actors, &candidates)
            }) {
                return Err(PlanIssue::new(
                    "deployment.concurrent_placement_unavailable",
                    "/tasks",
                    "live shared-world Task roles have no distinct endpoint assignment",
                ));
            }
        }
        if self.terminal_verifier_barrier {
            let verifier_tasks = plan
                .task_graph()
                .tasks()
                .iter()
                .filter(|task| {
                    matches!(
                        task.satisfaction_basis(),
                        domain::TaskSatisfactionBasis::VerifierEvidence(_)
                    )
                })
                .map(|task| task.task_id())
                .collect::<BTreeSet<_>>();
            let retained = session
                .slots
                .iter()
                .filter(|slot| verifier_tasks.contains(&slot.task_id))
                .map(|slot| slot.actor_id.clone())
                .collect::<Vec<_>>();
            // A verifier-backed local terminal retains its endpoint until the
            // producer has finished ALL slots. Reusing one Actor or oversubscribing
            // endpoint capacity therefore has no possible release order. Other
            // Tasks may reuse endpoints after ordinary satisfaction as before.
            if retained.iter().collect::<BTreeSet<_>>().len() != retained.len()
                || !has_distinct_endpoint_assignment(&retained, &candidates)
            {
                return Err(PlanIssue::new(
                    if retained.len() > self.node_agents.len() {
                        "verifier.retained_capacity_exceeded"
                    } else {
                        "verifier.retained_placement_unavailable"
                    },
                    "/tasks",
                    format!(
                        "final verifier resource lifetime has no feasible shared-world endpoint assignment: {} retained slots, {} configured capacity-one endpoints; final publication waits for all session slots",
                        retained.len(),
                        self.node_agents.len()
                    ),
                ));
            }
        }
        let actors = session
            .slots
            .iter()
            .map(|slot| &slot.actor_id)
            .collect::<BTreeSet<_>>();
        let tasks = session
            .slots
            .iter()
            .map(|slot| &slot.task_id)
            .collect::<BTreeSet<_>>();
        let supported_serial = actors.len() == 1 && tasks.len() == session.slots.len();
        let supported_pair = session.slots.len() == 2
            && actors.len() == 2
            && session
                .slots
                .iter()
                .all(|slot| slot.dependencies.is_empty())
            && candidates.values().next().is_some_and(|first| {
                candidates.values().nth(1).is_some_and(|second| {
                    first
                        .iter()
                        .any(|left| second.iter().any(|right| left != right))
                })
            });
        let supported_topology = if self.live_operations.is_some() {
            session.slots.len() <= 32
        } else {
            supported_serial || supported_pair
        };
        if !supported_topology {
            return Err(PlanIssue::new(
                if self.live_operations.is_some() {
                    "deployment.slot_limit_exceeded"
                } else {
                    "deployment.topology_unsupported"
                },
                "/tasks",
                "accepted-plan topology has no feasible shared-world endpoint assignment",
            ));
        }
        Ok(candidates.into_iter().collect())
    }

    /// Returns the immutable source digest for Control checkpoint attribution.
    pub(crate) fn digest(&self) -> &str {
        &self.digest
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Loads synthetic composition cases shared with Mission's closed contract consumer.
    fn composition_fixture() -> serde_json::Value {
        serde_json::from_str(include_str!(
            "../../../../contracts/mission/execution-planning-v0.2/fixtures/regression-cases.json"
        ))
        .unwrap()
    }

    /// Applies fixture edits only; no runtime recovery path rewrites an accepted draft.
    fn composition_plan(case: &serde_json::Value) -> serde_json::Value {
        let mut plan = composition_fixture()["plan"].clone();
        for change in case["changes"].as_array().unwrap() {
            let (parent, key) = change["path"].as_str().unwrap().rsplit_once('/').unwrap();
            plan.pointer_mut(parent)
                .unwrap()
                .as_object_mut()
                .unwrap()
                .insert(key.into(), change["value"].clone());
        }
        plan
    }

    /// Retains the original source validation with generic fixture entities and actual live support.
    fn composition_deployment(path: &Path) -> DeploymentFeasibility {
        finality_snapshot(path, true);
        let mut document: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        for record in document["records"].as_array_mut().unwrap() {
            record["destination"] = if record["destination"] == "any_targets|0" {
                "landmark-a"
            } else {
                "landmark-b"
            }
            .into();
        }
        for endpoint in document["execution_profile"]["endpoints"]
            .as_array_mut()
            .unwrap()
        {
            endpoint["operations"] = serde_json::json!([
                "mobility.navigate@v1",
                "observation.verify@v1",
                "observation.acquire@v1"
            ]);
        }
        seal(&mut document);
        std::fs::write(path, document.to_string()).unwrap();
        DeploymentFeasibility::load(path).unwrap()
    }

    /// Publishes a synthetic complete-goal feed without generating any world verdict.
    fn composition_feed(directory: &Path) -> super::super::task_verifier::TaskVerifierFeed {
        let mut source = serde_json::json!({
            "schema_version":"roboguide.task-verifier-source/v0.1",
            "source_id":"synthetic-final-world", "source_revision":format!("sha256:{}", "a".repeat(64)),
            "identity":{"run_id":"run", "episode_id":"synthetic", "scene_id":"scene",
                "dataset_revision":"synthetic", "dataset_sha256":"a".repeat(64)},
            "verifier":{"namespace":"observation","name":"verify","version":"v1"},
            "supported_predicates":[composition_fixture()["predicate"]], "verdict_finality":"terminal"
        });
        source["digest"] = serde_json::json!(format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(&source).unwrap())
        ));
        let path = directory.join("source.json");
        std::fs::write(&path, source.to_string()).unwrap();
        super::super::task_verifier::TaskVerifierFeed::load(
            &path,
            directory.join("no-verdict.json"),
        )
        .unwrap()
    }

    /// Effective modes, retained slots and real dependencies match Python without selecting Nodes.
    #[test]
    fn published_composition_cases_match_mission_contract_feedback() {
        let directory = tempfile::tempdir().unwrap();
        let deployment = composition_deployment(&directory.path().join("deployment.json"));
        let feed = composition_feed(directory.path());
        let group = domain::ExecutionGroupId::new("composition-group").unwrap();
        let fixture = composition_fixture();
        assert_eq!(
            deployment.published_topology().unwrap(),
            fixture["execution_topology"]
        );
        for case in fixture["cases"].as_array().unwrap() {
            let document = composition_plan(case);
            let original = document.clone();
            let plan = orchestration::decode_mission_plan(&document.to_string()).unwrap();
            let mut issues = feed.plan_issues(&plan);
            issues.extend(deployment.plan_issues(&plan, &group));
            let codes = issues
                .iter()
                .map(|issue| issue.code)
                .collect::<BTreeSet<_>>();
            let expected = case["expected_codes"]
                .as_array()
                .unwrap()
                .iter()
                .map(|code| code.as_str().unwrap())
                .collect::<BTreeSet<_>>();
            assert_eq!(codes, expected, "{}", case["id"]);
            let paths = issues
                .iter()
                .map(|issue| issue.path.as_str())
                .collect::<BTreeSet<_>>();
            let expected_paths = case
                .get("expected_paths")
                .and_then(serde_json::Value::as_array)
                .into_iter()
                .flatten()
                .map(|path| path.as_str().unwrap())
                .collect::<BTreeSet<_>>();
            assert_eq!(paths, expected_paths, "{}", case["id"]);
            assert_eq!(
                deployment.restrictions_for_plan(&plan, &group).is_ok(),
                expected.is_empty()
            );
            assert_eq!(document, original);
            let serialized = serde_json::to_string(&issues).unwrap();
            assert!(!serialized.contains("node-a") && !serialized.contains("node-b"));
        }
        let path = directory.path().join("legacy.json");
        snapshot(&path, true);
        assert!(
            DeploymentFeasibility::load(&path)
                .unwrap()
                .published_topology()
                .is_none()
        );
    }

    /// Real submission keeps a mode mismatch rejected before Groups, events or checkpoints exist.
    #[tokio::test]
    async fn unsupported_effective_mode_stays_rejected_at_submission() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("deployment.json");
        live_snapshot(&path, true);
        let deployment = DeploymentFeasibility::load(&path).unwrap();
        let mut document = plan_document(false, true);
        document["contexts"][0]["coupling_mode"] = "sequential-handoff".into();
        document["tasks"][0]
            .as_object_mut()
            .unwrap()
            .remove("coupling_mode");
        let decoded = orchestration::decode_mission_plan(&document.to_string()).unwrap();
        let group = domain::ExecutionGroupId::new("mode-group").unwrap();
        let issues = deployment.plan_issues(&decoded, &group);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].code, "deployment.coupling_mode_unsupported");
        assert_eq!(issues[0].path, "/contexts/0/coupling_mode");
        assert_http_topology_rejection(&deployment, document, (409, "Effective coupling mode"))
            .await;
    }

    /// Load the synthetic, language-shared cases; these are not B1 experiment results.
    fn support_regression_fixture() -> serde_json::Value {
        serde_json::from_str(include_str!(
            "../../../../contracts/mission/initial-operation-assessment-v0.2/fixtures/regression-cases.json"
        ))
        .unwrap()
    }

    /// Keep the first and later serial Tasks separate without changing either exact intent.
    fn support_regression_plan(
        fixture: &serde_json::Value,
        case: &serde_json::Value,
    ) -> serde_json::Value {
        let mut plan = fixture["plan"].clone();
        if case["serial"] == true {
            plan["mission"]["actors"] = serde_json::json!([{"id": "actor-a"}]);
            plan["contexts"][1]["roles"][0]["actor"] = serde_json::json!("actor-a");
            plan["tasks"][1]["depends_on"] = serde_json::json!([plan["tasks"][0]["id"]]);
        }
        plan
    }

    /// Construct source-bound observations from the fixed support matrix, not expected verdicts.
    fn support_regression_deployment(
        directory: &Path,
        case: &serde_json::Value,
    ) -> DeploymentFeasibility {
        let matrix_path = directory.join("reset.json");
        let mut matrix = snapshot(&matrix_path, true);
        for (index, record) in matrix["records"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .enumerate()
        {
            record["destination"] = serde_json::json!(if index < 2 {
                "landmark-a"
            } else {
                "landmark-b"
            });
        }
        seal(&mut matrix);
        std::fs::write(&matrix_path, matrix.to_string()).unwrap();
        let mut deployment = DeploymentFeasibility::load(&matrix_path).unwrap();
        let support = case["support"].as_array().unwrap();
        let misses = std::array::from_fn(|index| support[index] == "static-disjoint");
        let (mut source, mut projection) = geometry_preference_documents(&matrix, misses);
        source["identity"]["local_how_digest"] =
            serde_json::json!(format!("sha256:{}", "a".repeat(64)));
        for (index, kind) in support.iter().enumerate() {
            let record = &mut source["records"][index];
            if kind == "witnessed" {
                record["status"] = serde_json::json!("supported");
                record["selection"] = serde_json::json!({"path_length_m": 1.5});
                record["region_analysis"]["status"] = serde_json::json!("intersects");
                projection["records"][index]["cost_micrometers"] = serde_json::json!(1_500_000);
                projection["records"][index]["static_support"] = serde_json::json!("witnessed");
            } else if kind == "unknown" {
                record["status"] = serde_json::json!("unavailable");
            } else {
                assert!(kind == "bounded-miss" || kind == "static-disjoint");
            }
        }
        seal(&mut source);
        projection["source_digest"] = source["digest"].clone();
        seal(&mut projection);
        let source_path = directory.join("routes.json");
        let projection_path = directory.join("preferences.json");
        std::fs::write(&source_path, source.to_string()).unwrap();
        std::fs::write(&projection_path, projection.to_string()).unwrap();
        deployment
            .configure_initial_preferences(
                &projection_path,
                &source_path,
                domain::TimestampMs::new(0),
                true,
            )
            .unwrap();
        deployment.configure_initial_assessment().unwrap();
        deployment
    }

    /// Freeze receive-time expiry in actual HTTP tests without wall-clock races.
    struct AssessmentClock(domain::TimestampMs);

    impl ports::Clock for AssessmentClock {
        /// Return the one preregistered query time without renewing reset evidence.
        fn now(&self) -> domain::TimestampMs {
            self.0
        }
    }

    /// Compare the neutral producer reply to shared consumer expectations and source identities.
    fn assert_support_regression(
        result: &serde_json::Value,
        case: &serde_json::Value,
        plan: &serde_json::Value,
        directory: &Path,
        body: &str,
        now: domain::TimestampMs,
    ) {
        let expected = &case["expected"];
        for key in [
            "decision",
            "reason_code",
            "checked_combinations",
            "placement_failure",
        ] {
            assert_eq!(result[key], expected[key], "case={}, {result}", case["id"]);
        }
        assert_eq!(result["mission_id"], plan["mission"]["id"]);
        assert_eq!(
            result["plan_body_sha256"],
            format!("sha256:{:x}", Sha256::digest(body.as_bytes()))
        );
        assert_eq!(result["assessed_at_ms"], now.as_millis());
        assert_eq!(result["received_at_ms"], 0);
        assert_eq!(result["expires_at_ms"], 600_000);
        for (filename, key) in [
            ("preferences.json", "source_digest"),
            ("reset.json", "world_snapshot_digest"),
        ] {
            let source: serde_json::Value =
                serde_json::from_slice(&std::fs::read(directory.join(filename)).unwrap()).unwrap();
            assert_eq!(result[key], source["digest"]);
        }
        assert_eq!(
            result["local_how_digest"],
            format!("sha256:{}", "a".repeat(64))
        );
        let roles = result["roles"].as_array().unwrap();
        let counts = expected["role_counts"].as_array().unwrap();
        assert_eq!(roles.len(), counts.len());
        for (index, role) in roles.iter().enumerate() {
            assert_eq!(role["task_id"], plan["tasks"][index]["id"]);
            assert_eq!(role["role_id"], "navigator");
            assert_eq!(role["operation"], "mobility.navigate@v1");
            for (count_index, key) in [
                "witness_count",
                "bounded_miss_count",
                "scoped_disjoint_count",
                "unknown_count",
            ]
            .iter()
            .enumerate()
            {
                assert_eq!(
                    role[*key], counts[index][count_index],
                    "case={}, {result}",
                    case["id"]
                );
            }
        }
        let reports = result["candidate_diagnostics"].as_array().unwrap();
        assert_eq!(reports.len(), counts.len());
        for (index, report) in reports.iter().enumerate() {
            assert_eq!(report["task_id"], plan["tasks"][index]["id"]);
            assert_eq!(report["role_id"], "navigator");
            for key in ["considered_count", "eligible_count", "exclusions"] {
                assert_eq!(report[key], expected[key]);
            }
        }
        let schema: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../contracts/mission/initial-operation-assessment-v0.2/assessment.schema.json"
        ))
        .unwrap();
        assert_eq!(
            result.as_object().unwrap().keys().collect::<Vec<_>>(),
            schema["properties"]
                .as_object()
                .unwrap()
                .keys()
                .collect::<Vec<_>>()
        );
        assert_eq!(
            result["schema_version"],
            "roboguide.initial-operation-assessment/v0.2"
        );
        assert_eq!(result["scope"], "initial_static_world");
        assert!(!result.to_string().contains("node-a") && !result.to_string().contains("node-b"));
    }

    /// Freeze the original observation separately from its neutral cost projection.
    fn initial_route_document(
        matrix: &serde_json::Value,
        costs: [Option<u64>; 4],
    ) -> serde_json::Value {
        let mut identity = matrix["identity"].clone();
        identity["preassignment_digest"] = matrix["digest"].clone();
        let records = matrix["records"].as_array().unwrap().iter().zip(costs).map(|(record, cost)| serde_json::json!({
            "node_id": record["node_id"], "agent_id": record["agent_id"], "destination": record["destination"],
            "status": if cost.is_some() { "supported" } else { "not_found" },
            "selection": cost.map(|cost| serde_json::json!({"path_length_m": cost as f64 / 1_000_000.0})),
        })).collect::<Vec<_>>();
        let mut source = serde_json::json!({
            "schema_version": "roboguide.deployment-reset-route-support/v0.1", "authority": "deployment-observed-reset-state",
            "purpose": "diagnostic_only", "scope_status": "available", "identity": identity,
            "initial_agent_positions": matrix["initial_agent_positions"], "records": records,
        });
        seal(&mut source);
        source
    }

    /// Write exact operation costs without adding Mission or Actor selectors.
    fn initial_cost_document(
        matrix: &serde_json::Value,
        costs: [Option<u64>; 4],
    ) -> serde_json::Value {
        let records = matrix["records"].as_array().unwrap().iter().zip(costs).map(|(record, cost)| serde_json::json!({
            "operation": record["operation"], "parameters": {"destination": record["destination"]},
            "node_id": record["node_id"], "cost_micrometers": cost,
        })).collect::<Vec<_>>();
        let mut body = serde_json::json!({
            "schema_version": "roboguide.deployment-initial-operation-preferences/v0.1",
            "authority": "deployment-observed-reset-state", "scope": "initial_world_before_first_dispatch",
            "source_digest": initial_route_document(matrix, costs)["digest"], "feasibility_digest": matrix["digest"], "records": records,
        });
        seal(&mut body);
        body
    }

    /// Freeze scoped static misses separately from neutral initial candidate ordering.
    fn geometry_preference_documents(
        matrix: &serde_json::Value,
        misses: [bool; 4],
    ) -> (serde_json::Value, serde_json::Value) {
        let mut source = initial_route_document(matrix, [None; 4]);
        source["schema_version"] =
            serde_json::json!("roboguide.deployment-reset-route-support/v0.2");
        for (record, missing) in source["records"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .zip(misses)
        {
            record["official_robot_at_threshold_m"] = serde_json::json!(2.0);
            record["region_analysis"] = serde_json::json!({
                "schema_version": "roboguide.static-navigation-region/v0.1",
                "scope": "reset_static_start_component",
                "status": if missing { "disjoint" } else { "unknown" },
                "reason_code": if missing { "static_component_disjoint" } else { "triangle_budget" },
                "complete": missing, "triangles": 1,
                "triangles_examined": if missing { 1 } else { 0 },
                "orientation_safe_distance_lower_bound_m": if missing { serde_json::json!(5.0) } else { serde_json::Value::Null },
            });
        }
        seal(&mut source);
        let mut projection = initial_cost_document(matrix, [None; 4]);
        projection["schema_version"] =
            serde_json::json!("roboguide.deployment-initial-operation-preferences/v0.2");
        projection["source_digest"] = source["digest"].clone();
        for (record, missing) in projection["records"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .zip(misses)
        {
            record["static_support"] = serde_json::json!(if missing {
                "static-disjoint"
            } else {
                "unknown"
            });
        }
        seal(&mut projection);
        (source, projection)
    }

    /// Full Control preparation retains every eligible endpoint despite a static miss.
    #[test]
    fn initial_geometry_changes_order_without_exclusion_or_success() {
        for (misses, expected) in [
            ([true, false, false, false], "node-b"),
            ([true; 4], "node-a"),
            ([false; 4], "node-a"),
        ] {
            let directory = tempfile::tempdir().unwrap();
            let matrix_path = directory.path().join("reset.json");
            let matrix = snapshot(&matrix_path, true);
            let mut deployment = DeploymentFeasibility::load(&matrix_path).unwrap();
            let (source, projection) = geometry_preference_documents(&matrix, misses);
            let source_path = directory.path().join("routes.json");
            let costs_path = directory.path().join("costs.json");
            std::fs::write(&source_path, source.to_string()).unwrap();
            std::fs::write(&costs_path, projection.to_string()).unwrap();
            let now = domain::TimestampMs::new(0);
            deployment
                .configure_initial_preferences(&costs_path, &source_path, now, true)
                .unwrap();
            let plan = orchestration::decode_mission_plan(&plan_document(false, true).to_string())
                .unwrap();
            let mut events =
                state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
            let mut control = control::ControlPlane::new();
            let state = initial_cost_nodes(&mut control, &plan, &mut events);
            let correlation = domain::CorrelationId::new("geometry-order").unwrap();
            let mut orchestrator = orchestration::MissionOrchestrator::new();
            orchestrator
                .submit(
                    plan.clone(),
                    domain::ExecutionGroupId::new("geometry-group").unwrap(),
                    &mut control,
                    now,
                    &correlation,
                    &mut events,
                )
                .unwrap();
            let preference = deployment
                .initial_preferences_for_plan(
                    &plan,
                    &control,
                    &state,
                    now,
                    &correlation,
                    &mut events,
                )
                .unwrap()
                .unwrap();
            control
                .set_initial_candidate_preferences(&plan, preference)
                .unwrap();
            let first = &plan.task_graph().tasks()[0];
            let candidates = control
                .match_capabilities_for_mission(
                    &state,
                    &plan,
                    first.requirement(),
                    now,
                    &correlation,
                    &mut events,
                )
                .unwrap();
            assert_eq!(
                candidates
                    .for_role(first.requirement().roles()[0].role_id())
                    .unwrap()
                    .node_ids()
                    .len(),
                2
            );
            assert!(plan.actors().iter().all(|actor| {
                control
                    .actor_binding(plan.goal().mission_id(), actor.id())
                    .is_none()
            }));
            for (index, task) in plan.task_graph().tasks().iter().enumerate() {
                orchestrator
                    .prepare_task(
                        plan.goal().mission_id(),
                        task.requirement().task_ref(),
                        &state,
                        &mut control,
                        now,
                        &correlation,
                        &mut events,
                    )
                    .unwrap();
                let node = control
                    .actor_binding(
                        plan.goal().mission_id(),
                        task.requirement().roles()[0].actor_id().unwrap(),
                    )
                    .unwrap()
                    .node_id();
                assert_eq!(
                    node.as_str(),
                    if index == 0 {
                        expected
                    } else if expected == "node-a" {
                        "node-b"
                    } else {
                        "node-a"
                    }
                );
            }
            assert!(
                deployment
                    .initial_preferences_for_plan(
                        &plan,
                        &control,
                        &state,
                        domain::TimestampMs::new(control::MAX_INITIAL_PREFERENCE_AGE_MS),
                        &correlation,
                        &mut events
                    )
                    .unwrap()
                    .is_none()
            );
        }
    }

    /// Build a separately enabled assessment over synthetic source-bound geometry.
    fn assessment_fixture(
        directory: &Path,
        misses: [bool; 4],
        fresh: bool,
    ) -> DeploymentFeasibility {
        let matrix_path = directory.join("reset.json");
        let matrix = snapshot(&matrix_path, true);
        let mut deployment = DeploymentFeasibility::load(&matrix_path).unwrap();
        let (mut source, mut projection) = geometry_preference_documents(&matrix, misses);
        source["identity"]["local_how_digest"] =
            serde_json::json!(format!("sha256:{}", "a".repeat(64)));
        seal(&mut source);
        projection["source_digest"] = source["digest"].clone();
        seal(&mut projection);
        let source_path = directory.join("routes.json");
        let projection_path = directory.join("preferences.json");
        std::fs::write(&source_path, source.to_string()).unwrap();
        std::fs::write(&projection_path, projection.to_string()).unwrap();
        deployment
            .configure_initial_preferences(
                &projection_path,
                &source_path,
                domain::TimestampMs::new(0),
                fresh,
            )
            .unwrap();
        deployment.configure_initial_assessment().unwrap();
        deployment
    }

    /// A joint scoped shortage is observable without filtering or committing a Node.
    #[test]
    fn initial_assessment_preserves_control_and_distinguishes_scoped_misses() {
        for (misses, decision) in [
            ([false, true, false, true], "blocked"),
            ([true; 4], "blocked"),
            ([false; 4], "not_blocked"),
            ([true, false, false, true], "not_blocked"),
        ] {
            let directory = tempfile::tempdir().unwrap();
            let deployment = assessment_fixture(directory.path(), misses, true);
            let body = plan_document(false, true).to_string();
            let plan = orchestration::decode_mission_plan(&body).unwrap();
            let mut log =
                state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
            let mut control = control::ControlPlane::new();
            let state = initial_cost_nodes(&mut control, &plan, &mut log);
            let checkpoint = serde_json::to_string(&control.checkpoint()).unwrap();
            let sequence = log.latest_sequence().unwrap();
            let result = deployment.assess_initial_plan(
                &plan,
                &control,
                &state,
                domain::TimestampMs::new(0),
                true,
                body.as_bytes(),
            );
            assert_eq!(result["decision"], decision, "{result}");
            assert_eq!(result["checked_combinations"], 2);
            assert_eq!(result["roles"].as_array().unwrap().len(), 2);
            assert_eq!(
                result["plan_body_sha256"],
                format!("sha256:{:x}", Sha256::digest(body.as_bytes()))
            );
            assert_eq!(
                serde_json::to_string(&control.checkpoint()).unwrap(),
                checkpoint
            );
            assert_eq!(log.latest_sequence().unwrap(), sequence);
            assert!(control.actor_candidate_restrictions().next().is_none());
            assert!(
                !result.to_string().contains("node-a") && !result.to_string().contains("node-b")
            );
            if misses == [false; 4] {
                assert!(
                    result["roles"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .all(|role| role["bounded_miss_count"] == 2)
                );
            }
        }
    }

    /// Reset-only observations never authorize assessment after expiry, admission or restore.
    #[test]
    fn initial_assessment_lifetime_and_serial_scope_are_explicit() {
        let directory = tempfile::tempdir().unwrap();
        let deployment = assessment_fixture(directory.path(), [false, false, true, true], true);
        let body = plan_document(true, true).to_string();
        let plan = orchestration::decode_mission_plan(&body).unwrap();
        let mut log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let state = initial_cost_nodes(&mut control, &plan, &mut log);
        let result = deployment.assess_initial_plan(
            &plan,
            &control,
            &state,
            domain::TimestampMs::new(0),
            true,
            body.as_bytes(),
        );
        assert_eq!(result["decision"], "not_blocked");
        assert_eq!(result["roles"].as_array().unwrap().len(), 1);
        assert_eq!(result["checked_combinations"], 2);
        assert_eq!(
            deployment.assess_initial_plan(
                &plan,
                &control,
                &state,
                domain::TimestampMs::new(600000),
                true,
                body.as_bytes()
            )["reason_code"],
            "source_expired"
        );
        assert_eq!(
            deployment.assess_initial_plan(
                &plan,
                &control,
                &state,
                domain::TimestampMs::new(0),
                false,
                body.as_bytes()
            )["reason_code"],
            "world_already_admitted"
        );
        let restored = assessment_fixture(directory.path(), [false; 4], false);
        assert_eq!(
            restored.assess_initial_plan(
                &plan,
                &control,
                &state,
                domain::TimestampMs::new(0),
                true,
                body.as_bytes()
            )["reason_code"],
            "controller_restored"
        );
    }

    /// Positive, unknown and disabled sources remain separate from scoped negative evidence.
    #[test]
    fn initial_assessment_source_categories_and_default_off_are_explicit() {
        let directory = tempfile::tempdir().unwrap();
        let mut deployment = assessment_fixture(directory.path(), [false; 4], true);
        let source_path = directory.path().join("routes.json");
        let projection_path = directory.path().join("preferences.json");
        let mut source: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&source_path).unwrap()).unwrap();
        let mut projection: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&projection_path).unwrap()).unwrap();
        for record in source["records"].as_array_mut().unwrap() {
            record["status"] = serde_json::json!("unavailable");
        }
        source["records"][0]["status"] = serde_json::json!("supported");
        source["records"][0]["selection"] = serde_json::json!({"path_length_m": 1.5});
        seal(&mut source);
        projection["records"][0]["cost_micrometers"] = serde_json::json!(1_500_000);
        projection["records"][0]["static_support"] = serde_json::json!("witnessed");
        projection["source_digest"] = source["digest"].clone();
        seal(&mut projection);
        std::fs::write(&source_path, source.to_string()).unwrap();
        std::fs::write(&projection_path, projection.to_string()).unwrap();
        let now = domain::TimestampMs::new(0);
        deployment
            .configure_initial_preferences(&projection_path, &source_path, now, true)
            .unwrap();
        let body = plan_document(false, true).to_string();
        let plan = orchestration::decode_mission_plan(&body).unwrap();
        let mut log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let state = initial_cost_nodes(&mut control, &plan, &mut log);
        let result =
            deployment.assess_initial_plan(&plan, &control, &state, now, true, body.as_bytes());
        assert_eq!(result["decision"], "not_blocked");
        let roles = result["roles"].as_array().unwrap();
        assert_eq!(
            roles
                .iter()
                .map(|role| role["witness_count"].as_u64().unwrap())
                .sum::<u64>(),
            1
        );
        assert_eq!(
            roles
                .iter()
                .map(|role| role["unknown_count"].as_u64().unwrap())
                .sum::<u64>(),
            3
        );
        let empty = state::InMemorySharedNodeState::new();
        assert_eq!(
            deployment.assess_initial_plan(&plan, &control, &empty, now, true, body.as_bytes())["reason_code"],
            "current_candidates_unavailable"
        );
        deployment.assessment_enabled = false;
        assert_eq!(
            deployment.assess_initial_plan(&plan, &control, &state, now, true, body.as_bytes())["reason_code"],
            "source_unconfigured"
        );
        source["identity"]["local_how_digest"] = serde_json::Value::Null;
        seal(&mut source);
        projection["source_digest"] = source["digest"].clone();
        seal(&mut projection);
        std::fs::write(&source_path, source.to_string()).unwrap();
        std::fs::write(&projection_path, projection.to_string()).unwrap();
        deployment
            .configure_initial_preferences(&projection_path, &source_path, now, true)
            .unwrap();
        assert!(
            deployment
                .configure_initial_assessment()
                .unwrap_err()
                .contains("Local How")
        );
    }

    /// Shared positive/negative/unknown vectors cross real HTTP without changing Control authority.
    #[tokio::test]
    async fn initial_assessment_http_is_read_only_and_plan_bound() {
        use ports::SharedNodeStateWriter;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let fixture = support_regression_fixture();
        for case in fixture["cases"].as_array().unwrap() {
            let directory = tempfile::tempdir().unwrap();
            let deployment = support_regression_deployment(directory.path(), case);
            let clock = AssessmentClock(domain::TimestampMs::new(
                if case["condition"] == "expired" {
                    600_000
                } else {
                    100
                },
            ));
            let now = ports::Clock::now(&clock);
            let document = support_regression_plan(&fixture, case);
            let body = fixture["http_request_bodies"][if case["serial"] == true {
                "serial"
            } else {
                "parallel"
            }]
            .as_str()
            .unwrap()
            .to_owned();
            assert_eq!(
                serde_json::from_str::<serde_json::Value>(&body).unwrap(),
                document
            );
            let plan = orchestration::decode_mission_plan(&body).unwrap();
            let mut log =
                state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
            let mut control = control::ControlPlane::new();
            let mut state = initial_cost_nodes_at(&mut control, &plan, &mut log, now);
            if case["condition"] == "endpoint_offline" {
                state
                    .record_node_health(domain::NodeHealthObservation::new(
                        domain::NodeId::new("node-b").unwrap(),
                        domain::NodeStatus::new(domain::NodeHealth::Offline, now),
                        now,
                    ))
                    .unwrap();
            }
            let before_sequence = log.latest_sequence().unwrap();
            let controller = Arc::new(Mutex::new(ControllerState {
                federation: Default::default(),
                bridge: IntegrationRuntimeBridge::new(
                    control,
                    state,
                    log.clone(),
                    integration::GrpcNodeRouter::default(),
                ),
                orchestrator: MissionOrchestrator::new(),
                mission_admissions: BTreeMap::new(),
                memory_imports: BTreeMap::new(),
                memory_preparation: Default::default(),
                memory_use: Default::default(),
                memory_handoffs: BTreeMap::new(),
                runtime_revisions: Default::default(),
                hazards: application::hazards::HazardState::default(),
                safety_stops: BTreeMap::new(),
                verifier_seen: BTreeSet::new(),
                verifier_artifact_store: None,
                verifier_source_digest: None,
            }));
            let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
            let gate = Arc::new(Mutex::new(()));
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let server = async {
                let (mut stream, _) = listener.accept().await.unwrap();
                handle_http_connection(
                    &mut stream,
                    &controller,
                    &log,
                    &gate,
                    &clock,
                    Some(&deployment),
                    None,
                )
                .await
                .unwrap();
            };
            let client = async {
                let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
                stream.write_all(format!("POST /v1/missions/assess-initial-support HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
                let mut reply = String::new();
                stream.read_to_string(&mut reply).await.unwrap();
                let (header, response_body) = reply.split_once("\r\n\r\n").unwrap();
                assert!(header.starts_with("HTTP/1.1 200"));
                let result: serde_json::Value = serde_json::from_str(response_body).unwrap();
                assert_support_regression(&result, case, &document, directory.path(), &body, now);
                println!(
                    "initial-support-conformance {}",
                    serde_json::json!({
                        "case": case["id"], "request_body": body, "response": result,
                    })
                );
            };
            tokio::time::timeout(Duration::from_secs(5), async {
                tokio::join!(server, client)
            })
            .await
            .unwrap();
            assert_eq!(
                server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
                before
            );
            assert_eq!(log.latest_sequence().unwrap(), before_sequence);
            assert!(log.load_checkpoint().unwrap().is_none());
        }
    }

    /// Candidate shortage retains the exact query-time reason instead of later health guesses.
    #[test]
    fn initial_assessment_candidate_exclusions_are_query_bound() {
        use ports::{SharedNodeStateReader, SharedNodeStateWriter};
        let directory = tempfile::tempdir().unwrap();
        let deployment = assessment_fixture(directory.path(), [false; 4], true);
        let body = plan_document(false, true).to_string();
        let plan = orchestration::decode_mission_plan(&body).unwrap();
        let mut log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let mut state = initial_cost_nodes(&mut control, &plan, &mut log);
        let node = domain::NodeId::new("node-b").unwrap();
        state
            .record_node_health(domain::NodeHealthObservation::new(
                node.clone(),
                domain::NodeStatus::new(domain::NodeHealth::Offline, domain::TimestampMs::new(0)),
                domain::TimestampMs::new(0),
            ))
            .unwrap();
        let before = serde_json::to_string(&control.checkpoint()).unwrap();
        let sequence = log.latest_sequence().unwrap();
        let result = deployment.assess_initial_plan(
            &plan,
            &control,
            &state,
            domain::TimestampMs::new(0),
            true,
            body.as_bytes(),
        );
        assert_eq!(result["decision"], "unavailable");
        assert_eq!(result["placement_failure"], "endpoint_cardinality");
        for report in result["candidate_diagnostics"].as_array().unwrap() {
            assert_eq!(report["considered_count"], 2);
            assert_eq!(report["eligible_count"], 1);
            assert_eq!(report["exclusions"]["health_unschedulable"], 1);
        }
        // A later healthy observation cannot rewrite the earlier reason.
        state
            .record_node_health(domain::NodeHealthObservation::new(
                node,
                domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(1)),
                domain::TimestampMs::new(1),
            ))
            .unwrap();
        let later = deployment.assess_initial_plan(
            &plan,
            &control,
            &state,
            domain::TimestampMs::new(1),
            true,
            body.as_bytes(),
        );
        assert_eq!(later["decision"], "not_blocked");
        assert_eq!(result["candidate_diagnostics"][0]["eligible_count"], 1);
        assert_eq!(
            serde_json::to_string(&control.checkpoint()).unwrap(),
            before
        );
        assert_eq!(log.latest_sequence().unwrap(), sequence);
        assert_eq!(state.nodes().len(), 2);
        assert!(!result.to_string().contains("node-a") && !result.to_string().contains("node-b"));
    }

    /// A recomputed digest cannot turn partial geometry or a different source into a miss.
    #[test]
    fn initial_geometry_rejects_forged_projection_and_partial_negative() {
        for fault in [
            "partial",
            "wrong_bound",
            "wrong_scope",
            "wrong_version",
            "projection",
            "cost",
            "missing",
        ] {
            let directory = tempfile::tempdir().unwrap();
            let path = directory.path().join("reset.json");
            let matrix = snapshot(&path, true);
            let mut deployment = DeploymentFeasibility::load(&path).unwrap();
            let (mut source, mut projection) =
                geometry_preference_documents(&matrix, [true, false, false, false]);
            match fault {
                "partial" => {
                    source["records"][0]["region_analysis"]["complete"] = serde_json::json!(false)
                }
                "wrong_bound" => {
                    source["records"][0]["region_analysis"]["orientation_safe_distance_lower_bound_m"] =
                        serde_json::json!(1.0)
                }
                "wrong_scope" => {
                    source["records"][0]["region_analysis"]["scope"] =
                        serde_json::json!("global_impossibility")
                }
                "wrong_version" => {
                    source["schema_version"] =
                        serde_json::json!("roboguide.deployment-reset-route-support/v0.1")
                }
                "projection" => {
                    projection["records"][0]["static_support"] = serde_json::json!("unknown")
                }
                "cost" => projection["records"][0]["cost_micrometers"] = serde_json::json!(1),
                "missing" => {
                    source["records"][0]
                        .as_object_mut()
                        .unwrap()
                        .remove("region_analysis");
                }
                _ => unreachable!(),
            }
            seal(&mut source);
            projection["source_digest"] = source["digest"].clone();
            seal(&mut projection);
            let source_path = directory.path().join("routes.json");
            let costs_path = directory.path().join("costs.json");
            std::fs::write(&source_path, source.to_string()).unwrap();
            std::fs::write(&costs_path, projection.to_string()).unwrap();
            assert!(
                deployment
                    .configure_initial_preferences(
                        &costs_path,
                        &source_path,
                        domain::TimestampMs::new(0),
                        true
                    )
                    .is_err(),
                "{fault}"
            );
        }
    }

    /// Resealed missing or malformed versions must reject startup evidence without panic.
    #[test]
    fn initial_preferences_reject_missing_and_malformed_schema_without_panic() {
        for geometry in [false, true] {
            for fault in ["missing", "null", "non_string"] {
                let directory = tempfile::tempdir().unwrap();
                let path = directory.path().join("reset.json");
                let matrix = snapshot(&path, true);
                let mut deployment = DeploymentFeasibility::load(&path).unwrap();
                let (source, mut projection) = if geometry {
                    geometry_preference_documents(&matrix, [true, false, false, false])
                } else {
                    (
                        initial_route_document(&matrix, [Some(1); 4]),
                        initial_cost_document(&matrix, [Some(1); 4]),
                    )
                };
                match fault {
                    "missing" => {
                        projection.as_object_mut().unwrap().remove("schema_version");
                    }
                    "null" => projection["schema_version"] = serde_json::Value::Null,
                    "non_string" => projection["schema_version"] = serde_json::json!(2),
                    _ => unreachable!(),
                }
                seal(&mut projection);
                let source_path = directory.path().join("routes.json");
                let costs_path = directory.path().join("costs.json");
                std::fs::write(&source_path, source.to_string()).unwrap();
                std::fs::write(&costs_path, projection.to_string()).unwrap();
                assert!(
                    deployment
                        .configure_initial_preferences(
                            &costs_path,
                            &source_path,
                            domain::TimestampMs::new(0),
                            true
                        )
                        .is_err(),
                    "geometry={geometry}, fault={fault}"
                );
            }
        }
    }

    /// Register current capability and resource facts for initial policy tests.
    fn initial_cost_nodes(
        control: &mut control::ControlPlane,
        plan: &domain::MissionPlan,
        events: &mut state::SqliteEventLog,
    ) -> state::InMemorySharedNodeState {
        initial_cost_nodes_at(control, plan, events, domain::TimestampMs::new(0))
    }

    /// Register source-relative facts in the same time domain as an HTTP query.
    fn initial_cost_nodes_at(
        control: &mut control::ControlPlane,
        plan: &domain::MissionPlan,
        events: &mut state::SqliteEventLog,
        now: domain::TimestampMs,
    ) -> state::InMemorySharedNodeState {
        let mut state = state::InMemorySharedNodeState::new();
        let contracts: Vec<domain::CapabilityContractRef> = plan
            .task_graph()
            .tasks()
            .iter()
            .flat_map(|task| task.requirement().roles())
            .flat_map(|role| {
                role.capability_requirements()
                    .iter()
                    .map(|capability| capability.contract().clone())
                    .collect::<Vec<_>>()
            })
            .collect();
        for (node, resource) in [("node-a", "space-a"), ("node-b", "space-b")] {
            let registration = domain::NodeRegistration::new_with_contracts(
                domain::NodeId::new(node).unwrap(),
                domain::LocalRuntime::new("policy-test", "1").unwrap(),
                domain::NodeContractVersion::v0_1(),
                vec![domain::Capability::new(
                    domain::CapabilityKind::Mobility,
                    true,
                )],
                contracts.clone(),
                vec![
                    domain::Resource::new(
                        domain::ResourceId::new(resource).unwrap(),
                        domain::ResourceKind::Space,
                        1,
                    )
                    .unwrap(),
                ],
            );
            control
                .register_node(
                    &mut state,
                    registration,
                    domain::NodeStatus::new(domain::NodeHealth::Online, now),
                    now,
                    &domain::CorrelationId::new("initial-policy").unwrap(),
                    events,
                )
                .unwrap();
        }
        state
    }

    /// Coverage precedes distance for parallel roots; reversing Task order stays safe.
    #[test]
    fn initial_costs_support_joint_coverage_without_binding_or_exclusion() {
        for (costs, reverse, expected_first) in [
            (
                [Some(17_884_000), Some(13_191_000), Some(5_577_000), None],
                false,
                "node-b",
            ),
            (
                [Some(23_780_000), None, Some(2_224_000), Some(5_144_000)],
                false,
                "node-a",
            ),
            (
                [Some(23_780_000), None, Some(2_224_000), Some(5_144_000)],
                true,
                "node-b",
            ),
            ([None; 4], false, "node-a"),
        ] {
            let directory = tempfile::tempdir().unwrap();
            let path = directory.path().join("reset.json");
            let matrix = snapshot(&path, true);
            let mut deployment = DeploymentFeasibility::load(&path).unwrap();
            let costs_path = directory.path().join("costs.json");
            let source_path = directory.path().join("routes.json");
            std::fs::write(
                &source_path,
                initial_route_document(&matrix, costs).to_string(),
            )
            .unwrap();
            std::fs::write(
                &costs_path,
                initial_cost_document(&matrix, costs).to_string(),
            )
            .unwrap();
            deployment
                .configure_initial_preferences(
                    &costs_path,
                    &source_path,
                    domain::TimestampMs::new(0),
                    true,
                )
                .unwrap();
            let mut document = plan_document(false, true);
            if reverse {
                document["tasks"].as_array_mut().unwrap().reverse();
            }
            let plan = orchestration::decode_mission_plan(&document.to_string()).unwrap();
            let mut events =
                state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
            let mut control = control::ControlPlane::new();
            let state = initial_cost_nodes(&mut control, &plan, &mut events);
            let now = domain::TimestampMs::new(0);
            let correlation = domain::CorrelationId::new("initial-policy").unwrap();
            let mut orchestrator = orchestration::MissionOrchestrator::new();
            orchestrator
                .submit(
                    plan.clone(),
                    domain::ExecutionGroupId::new("initial-group").unwrap(),
                    &mut control,
                    now,
                    &correlation,
                    &mut events,
                )
                .unwrap();
            let preference = deployment
                .initial_preferences_for_plan(
                    &plan,
                    &control,
                    &state,
                    now,
                    &correlation,
                    &mut events,
                )
                .unwrap()
                .unwrap();
            control
                .set_initial_candidate_preferences(&plan, preference)
                .unwrap();
            assert!(plan.actors().iter().all(|actor| {
                control
                    .actor_binding(plan.goal().mission_id(), actor.id())
                    .is_none()
            }));
            for (index, task) in plan.task_graph().tasks().iter().enumerate() {
                let disposition = orchestrator
                    .prepare_task(
                        plan.goal().mission_id(),
                        task.requirement().task_ref(),
                        &state,
                        &mut control,
                        now,
                        &correlation,
                        &mut events,
                    )
                    .unwrap();
                assert_eq!(disposition.task_ref(), task.requirement().task_ref());
                let node = control
                    .actor_binding(
                        plan.goal().mission_id(),
                        task.requirement().roles()[0].actor_id().unwrap(),
                    )
                    .unwrap()
                    .node_id();
                assert_eq!(
                    node.as_str(),
                    if index == 0 {
                        expected_first
                    } else if expected_first == "node-a" {
                        "node-b"
                    } else {
                        "node-a"
                    }
                );
            }
        }
    }

    /// Restart, expiry and missing current providers leave the default policy intact.
    #[test]
    fn initial_costs_are_not_renewed_or_used_for_later_serial_tasks() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reset.json");
        let matrix = snapshot(&path, true);
        let mut deployment = DeploymentFeasibility::load(&path).unwrap();
        let costs_path = directory.path().join("costs.json");
        let source_path = directory.path().join("routes.json");
        std::fs::write(
            &source_path,
            initial_route_document(&matrix, [None, Some(1), Some(1), None]).to_string(),
        )
        .unwrap();
        std::fs::write(
            &costs_path,
            initial_cost_document(&matrix, [None, Some(1), Some(1), None]).to_string(),
        )
        .unwrap();
        let plan = plan(true, true);
        let mut events =
            state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let state = initial_cost_nodes(&mut control, &plan, &mut events);
        let now = domain::TimestampMs::new(0);
        let correlation = domain::CorrelationId::new("initial-policy").unwrap();
        for (fresh, time) in [(false, 0), (true, control::MAX_INITIAL_PREFERENCE_AGE_MS)] {
            deployment
                .configure_initial_preferences(&costs_path, &source_path, now, fresh)
                .unwrap();
            assert!(
                deployment
                    .initial_preferences_for_plan(
                        &plan,
                        &control,
                        &state,
                        domain::TimestampMs::new(time),
                        &correlation,
                        &mut events
                    )
                    .unwrap()
                    .is_none()
            );
        }
        deployment
            .configure_initial_preferences(&costs_path, &source_path, now, true)
            .unwrap();
        let preference = deployment
            .initial_preferences_for_plan(&plan, &control, &state, now, &correlation, &mut events)
            .unwrap()
            .unwrap();
        control
            .set_initial_candidate_preferences(&plan, preference)
            .unwrap();
        let later = &plan.task_graph().tasks()[1];
        let candidates = control
            .match_capabilities_for_mission(
                &state,
                &plan,
                later.requirement(),
                now,
                &correlation,
                &mut events,
            )
            .unwrap();
        let decision = control::BoundedJointScheduler::new()
            .schedule_task(
                &state,
                later.requirement(),
                &candidates,
                now,
                &correlation,
                &mut events,
            )
            .unwrap();
        assert_eq!(decision.selections()[0].node_id().as_str(), "node-a");
        assert!(
            deployment
                .initial_preferences_for_plan(
                    &plan,
                    &control,
                    &state::InMemorySharedNodeState::new(),
                    now,
                    &correlation,
                    &mut events
                )
                .unwrap()
                .is_none()
        );
    }

    /// Recomputed projection digests cannot hide source, parameter or coverage mistakes.
    #[test]
    fn initial_cost_source_rejects_invalid_scope_coverage_and_values() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reset.json");
        let matrix = snapshot(&path, true);
        let mut deployment = DeploymentFeasibility::load(&path).unwrap();
        let costs_path = directory.path().join("costs.json");
        let source_path = directory.path().join("routes.json");
        std::fs::write(
            &source_path,
            initial_route_document(&matrix, [Some(1); 4]).to_string(),
        )
        .unwrap();
        for fault in [
            "source",
            "parameters",
            "duplicate",
            "missing",
            "negative",
            "overflow",
            "schema",
            "digest",
            "laundered_cost",
        ] {
            let mut document = initial_cost_document(&matrix, [Some(1); 4]);
            match fault {
                "source" => {
                    document["feasibility_digest"] =
                        serde_json::json!(format!("sha256:{}", "0".repeat(64)))
                }
                "parameters" => {
                    document["records"][0]["parameters"]["extra"] = serde_json::json!("injected")
                }
                "duplicate" => {
                    let record = document["records"][0].clone();
                    document["records"].as_array_mut().unwrap().push(record);
                }
                "missing" => {
                    document["records"].as_array_mut().unwrap().pop();
                }
                "negative" => document["records"][0]["cost_micrometers"] = serde_json::json!(-1),
                "overflow" => {
                    document["records"][0]["cost_micrometers"] =
                        serde_json::json!(1_000_000_000_001u64)
                }
                "laundered_cost" => {
                    document["records"][0]["cost_micrometers"] = serde_json::json!(2)
                }
                "schema" => document["schema_version"] = serde_json::json!("wrong"),
                _ => {}
            }
            seal(&mut document);
            if fault == "digest" {
                document["digest"] = serde_json::json!("wrong");
            }
            std::fs::write(&costs_path, document.to_string()).unwrap();
            assert!(
                deployment
                    .configure_initial_preferences(
                        &costs_path,
                        &source_path,
                        domain::TimestampMs::new(0),
                        true
                    )
                    .is_err(),
                "{fault}"
            );
        }
    }

    /// Full catalogs may include entities outside the probe scope; their cost stays null.
    #[test]
    fn initial_costs_keep_unprobed_catalog_entities_unknown() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reset.json");
        let mut matrix = snapshot(&path, true);
        let extra: Vec<_> = matrix["records"].as_array().unwrap()[..2]
            .iter()
            .map(|record| {
                let mut record = record.clone();
                record["destination"] = serde_json::json!("unprobed-entity");
                record
            })
            .collect();
        matrix["records"]
            .as_array_mut()
            .unwrap()
            .extend(extra.clone());
        seal(&mut matrix);
        std::fs::write(&path, matrix.to_string()).unwrap();
        let mut deployment = DeploymentFeasibility::load(&path).unwrap();
        let source_path = directory.path().join("routes.json");
        std::fs::write(
            &source_path,
            initial_route_document(&matrix, [Some(1); 4]).to_string(),
        )
        .unwrap();
        let mut document = initial_cost_document(&matrix, [Some(1); 4]);
        document["records"].as_array_mut().unwrap().extend(extra.iter().map(|record| serde_json::json!({
            "operation": record["operation"], "parameters": {"destination": record["destination"]},
            "node_id": record["node_id"], "cost_micrometers": null,
        })));
        seal(&mut document);
        let costs_path = directory.path().join("costs.json");
        std::fs::write(&costs_path, document.to_string()).unwrap();
        deployment
            .configure_initial_preferences(
                &costs_path,
                &source_path,
                domain::TimestampMs::new(0),
                true,
            )
            .unwrap();
        document["records"][4]["cost_micrometers"] = serde_json::json!(1);
        seal(&mut document);
        std::fs::write(&costs_path, document.to_string()).unwrap();
        assert!(
            deployment
                .configure_initial_preferences(
                    &costs_path,
                    &source_path,
                    domain::TimestampMs::new(0),
                    true
                )
                .is_err()
        );
    }

    /// Python and Rust hash the same floats even when exponent syntax differs.
    #[test]
    fn python_float_digest_golden_is_stable() {
        let body: serde_json::Value = serde_json::from_str(r#"{"a":[1e-07,-0.0,1.0],"b":"目标"}"#)
            .expect("golden JSON decodes");
        assert_eq!(
            content_digest(&body).expect("digest computes"),
            "sha256:1ddb467cd0b0f8a661f022b93bae56e7dd457c530d75f55e03fbc3d84724fd8c"
        );
        let reset_decimal: serde_json::Value =
            serde_json::from_str(r#"{"a":[-1.9126900434494019]}"#)
                .expect("actual reset decimal parses exactly");
        assert_eq!(
            content_digest(&reset_decimal).expect("digest computes"),
            "sha256:d7c35e78037b280f3263068c3ad0c72bcf2f2e515160495244441c6fbe5e135f"
        );
    }

    /// Adapt one checked-in scenario to a v0.8 submission without provider calls.
    fn plan_document(serial: bool, space: bool) -> serde_json::Value {
        let mut document: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scenarios/e1-shared-world-episode-51/mission-plan.json"
        ))
        .expect("fixture is JSON");
        document["schema_version"] = serde_json::json!("roboguide.mission-plan/v0.8");
        for context in document["contexts"].as_array_mut().expect("contexts") {
            context["executor_constraints"] = serde_json::json!([]);
            if serial {
                context["roles"][0]["actor"] = serde_json::json!("one-robot");
            }
        }
        if serial {
            document["mission"]["actors"] = serde_json::json!([{"id": "one-robot"}]);
            document["tasks"][1]["depends_on"] = serde_json::json!([document["tasks"][0]["id"]]);
        }
        if !space {
            for task in document["tasks"].as_array_mut().expect("tasks") {
                task["roles"][0]["requirements"]["resources"] = serde_json::json!([]);
            }
        }
        document
    }

    /// Derive both supported shared-world topologies from a contract-valid fixture.
    fn plan(serial: bool, space: bool) -> domain::MissionPlan {
        orchestration::decode_mission_plan(&plan_document(serial, space).to_string())
            .expect("plan decodes")
    }

    /// Combine two independent role intents into one concurrently dispatched Task.
    fn multi_role_plan_document() -> serde_json::Value {
        let mut document = plan_document(false, true);
        let second_context_role = document["contexts"][1]["roles"][0].clone();
        document["contexts"][0]
            .get_mut("roles")
            .expect("context roles")
            .as_array_mut()
            .expect("context roles array")
            .push(second_context_role);
        let second_role = document["tasks"][1]["roles"][0].clone();
        document["tasks"][0]
            .get_mut("roles")
            .expect("task roles")
            .as_array_mut()
            .expect("task roles array")
            .push(second_role);
        document["tasks"].as_array_mut().expect("tasks").remove(1);
        document["contexts"]
            .as_array_mut()
            .expect("contexts")
            .remove(1);
        document["tasks"][0]["description"] =
            serde_json::json!("Navigate two independent Actors to the two semantic targets.");
        document
    }

    /// Write a complete reset observation with one or two feasible endpoint choices.
    fn snapshot(path: &Path, second_goal_on_b: bool) -> serde_json::Value {
        let mut records = Vec::new();
        for destination in ["any_targets|0", "TARGET_any_targets|0"] {
            for (agent_id, node_id, start_floor, support) in [
                (0, "node-a", "floor-1", true),
                (1, "node-b", "floor-3", second_goal_on_b),
            ] {
                let destination_floor = if destination == "any_targets|0" {
                    "floor-2"
                } else {
                    "floor-1"
                };
                let compatible = start_floor == destination_floor || support;
                records.push(serde_json::json!({
                    "schema_version": "roboguide.habitat-spatial-feasibility/v0.2",
                    "node_id": node_id,
                    "agent_id": agent_id,
                    "operation": "mobility.navigate@v1",
                    "destination": destination,
                    "profile": {
                        "node_id": node_id,
                        "agent_id": agent_id,
                        "source_digest": format!("sha256:{}", "0".repeat(64)),
                        "operation_support": {
                            "mobility.move@v1": support,
                            "mobility.navigate@v1": support
                        }
                    },
                    "goal_occupancy": "none",
                    "goal_tolerance_m": null,
                    "start": {
                        "position": [0.0, agent_id as f64, 0.0],
                        "region_id": null,
                        "floor_id": start_floor
                    },
                    "destination_entity": {
                        "position": [1.0, 0.0, 0.0],
                        "region_id": null,
                        "floor_id": destination_floor
                    },
                    "status": if compatible { "compatible" } else { "incompatible" },
                    "reason": "offline fixture",
                    "route_reachability_proven": false
                }));
            }
        }
        let mut body = serde_json::json!({
            "schema_version": "roboguide.deployment-intent-feasibility/v0.3",
            "authority": "deployment-observed-reset-state",
            "identity": {
                "run_id": "run",
                "episode_id": "episode",
                "scene_id": "scene",
                "dataset_revision": "dataset",
                "dataset_sha256": "0".repeat(64),
                "semantic_evidence_digest": format!("sha256:{}", "0".repeat(64)),
                "spatial_profile_digest": format!("sha256:{}", "0".repeat(64)),
                "habitat_seed": 40,
                "episode_reset_count": 1
            },
            "initial_agent_positions": {"0": [0.0, 0.0, 0.0], "1": [0.0, 1.0, 0.0]},
            "records": records
        });
        seal(&mut body);
        std::fs::write(path, body.to_string()).expect("fixture writes");
        body
    }

    /// Recompute only the archive-integrity digest after a deterministic fixture edit.
    fn seal(document: &mut serde_json::Value) {
        let body = document.as_object_mut().expect("object");
        body.remove("digest");
        document["digest"] = serde_json::json!(content_digest(document).expect("canonical JSON"));
    }

    /// A pair with only one feasible endpoint is rejected before Control submission.
    #[test]
    fn pair_shortage_is_explicit_while_one_actor_can_reuse_the_capable_endpoint() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("reset.json");
        snapshot(&path, false);
        let evidence = DeploymentFeasibility::load(&path).expect("valid source");
        let group = domain::ExecutionGroupId::new("group").expect("group id valid");
        assert!(
            evidence
                .restrictions_for_plan(&plan(false, true), &group)
                .expect_err("two Actors cannot share one endpoint")
                .contains("topology")
        );
        let serial = evidence
            .restrictions_for_plan(&plan(true, true), &group)
            .expect("one Actor retains one endpoint across tasks");
        assert_eq!(serial.len(), 1);
        assert_eq!(
            serial[0]
                .1
                .iter()
                .map(domain::NodeId::as_str)
                .collect::<Vec<_>>(),
            vec!["node-a"]
        );
        snapshot(&path, true);
        let evidence = DeploymentFeasibility::load(&path).expect("new valid source");
        let pair = evidence
            .restrictions_for_plan(&plan(false, true), &group)
            .expect("two Nodes can realize two Actors");
        assert_eq!(pair.len(), 2);
        assert!(
            pair.iter()
                .any(|(_, nodes)| nodes.contains(&domain::NodeId::new("node-b").unwrap()))
        );
        assert!(
            evidence
                .restrictions_for_plan(&plan(false, false), &group)
                .expect_err("a pair without endpoint exclusion can deadlock")
                .contains("space:1")
        );
    }

    /// One independent Task may carry two concurrent Roles when each has a distinct Actor.
    #[test]
    fn live_profile_admits_multi_role_task_with_distinct_endpoints() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("reset.json");
        live_snapshot(&path, true);
        let evidence = DeploymentFeasibility::load(&path).expect("valid source");
        let group = domain::ExecutionGroupId::new("group").expect("group id valid");
        let document = multi_role_plan_document();
        let decoded = orchestration::decode_mission_plan(&document.to_string())
            .expect("multi-role plan decodes");
        let restrictions = evidence
            .restrictions_for_plan(&decoded, &group)
            .expect("distinct endpoint candidates are available");
        assert_eq!(restrictions.len(), 2);
        for count in [16, 17] {
            let mut bounded = document.clone();
            let template = bounded["tasks"][0].clone();
            bounded["tasks"] = serde_json::Value::Array(
                (0..count)
                    .map(|index| {
                        let mut task = template.clone();
                        task["id"] = serde_json::json!(format!("multi-role-{index}"));
                        task
                    })
                    .collect(),
            );
            let plan = orchestration::decode_mission_plan(&bounded.to_string()).unwrap();
            assert_eq!(
                evidence.restrictions_for_plan(&plan, &group).is_ok(),
                count == 16,
                "the bound counts {} Roles across {count} Tasks",
                count * 2
            );
        }

        live_snapshot(&path, false);
        let shortage = DeploymentFeasibility::load(&path).expect("valid source");
        assert!(
            shortage
                .restrictions_for_plan(&decoded, &group)
                .expect_err("one endpoint cannot satisfy concurrent Roles")
                .contains("distinct endpoint assignment")
        );

        let mut same_actor = multi_role_plan_document();
        same_actor["contexts"][0]["roles"][1]["actor"] = serde_json::json!("shared-robot-a");
        let same_actor_plan = orchestration::decode_mission_plan(&same_actor.to_string())
            .expect("same-Actor multi-role plan decodes");
        let evidence = DeploymentFeasibility::load(&path).expect("valid source");
        assert!(
            evidence
                .restrictions_for_plan(&same_actor_plan, &group)
                .expect_err("one Actor cannot own concurrent Roles")
                .contains("distinct logical Actors")
        );
    }

    /// Real HTTP dispatch and ordered Runtime facts retain multi-role DAG and resource authority.
    #[tokio::test]
    async fn live_multi_role_followup_uses_control_bindings_and_waits_for_all_roles() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("live.json");
        let mut source = live_snapshot(&path, true);
        source["execution_profile"]["endpoints"][1]["operations"] =
            serde_json::json!(["mobility.navigate@v1", "observation.verify@v1"]);
        seal(&mut source);
        std::fs::write(&path, source.to_string()).unwrap();
        let feasibility = DeploymentFeasibility::load(&path).unwrap();
        let multi = multi_role_plan_document();
        let mut document = plan_document(false, true);
        document["contexts"] = multi["contexts"].clone();
        document["tasks"][1]["context_id"] = document["tasks"][0]["context_id"].clone();
        let mut followup = multi["tasks"][0].clone();
        followup["id"] = "observe-both".into();
        followup["depends_on"] =
            serde_json::json!([document["tasks"][0]["id"], document["tasks"][1]["id"]]);
        for role in followup["roles"].as_array_mut().unwrap() {
            let destination = role["execution_intent"]["parameters"]["destination"]
                .as_str()
                .unwrap()
                .to_owned();
            let operation =
                serde_json::json!({"namespace":"observation", "name":"verify", "version":"v1"});
            role["execution_intent"]["operation"] = operation.clone();
            role["execution_intent"]["parameters"] =
                serde_json::json!({"expected": format!("detected({destination})")});
            role["requirements"]["capabilities"][0]["contract"] = operation;
        }
        // Synthetic execution-report satisfaction exercises Control, not official benchmark truth.
        document["tasks"].as_array_mut().unwrap().push(followup);
        let plan = orchestration::decode_mission_plan(&document.to_string()).unwrap();
        let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let state = initial_cost_nodes(&mut control, &plan, &mut events.clone());
        let controller = Arc::new(Mutex::new(ControllerState {
            federation: Default::default(),
            bridge: IntegrationRuntimeBridge::new(
                control,
                state,
                events.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator: MissionOrchestrator::new(),
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            memory_preparation: Default::default(),
            memory_use: Default::default(),
            memory_handoffs: BTreeMap::new(),
            runtime_revisions: Default::default(),
            hazards: application::hazards::HazardState::default(),
            safety_stops: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_artifact_store: None,
            verifier_source_digest: None,
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let gate = Arc::new(Mutex::new(()));
        let clock = runtime::FixedClock::new(domain::TimestampMs::new(1));
        let body = document.to_string();
        let request = format!(
            "POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        let server = async {
            let (mut stream, _) = listener.accept().await.unwrap();
            handle_http_connection(
                &mut stream,
                &controller,
                &events,
                &gate,
                &clock,
                Some(&feasibility),
                None,
            )
            .await
            .unwrap();
        };
        let client = async {
            let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
            stream.write_all(request.as_bytes()).await.unwrap();
            let mut response = String::new();
            stream.read_to_string(&mut response).await.unwrap();
            response
        };
        let response = tokio::time::timeout(Duration::from_secs(5), async {
            tokio::join!(server, client)
        })
        .await
        .unwrap()
        .1;
        assert!(response.starts_with("HTTP/1.1 202"), "{response}");
        let mut controller = controller.lock().unwrap();
        let roots = controller.bridge.attempt_history();
        assert_eq!(roots.len(), 2);
        assert_ne!(roots[0].command().node_id(), roots[1].command().node_id());
        for index in 0..4 {
            let attempts = controller.bridge.attempt_history();
            let attempt = if index < 2 {
                &roots[index]
            } else {
                attempts
                    .iter()
                    .find(|attempt| {
                        attempt.command().task_ref().task_id().as_str() == "observe-both"
                            && attempt.command().role_id() == roots[index - 2].command().role_id()
                    })
                    .unwrap()
            };
            let command = attempt.command();
            let resources = controller
                .bridge
                .control()
                .group(command.group_id())
                .unwrap()
                .task_execution(command.task_ref())
                .unwrap()
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == command.role_id())
                .unwrap()
                .resource_ids();
            assert_eq!(resources.len(), 1);
            assert_eq!(
                resources[0].to_string(),
                command.node_id().to_string().replace("node-", "space-")
            );
            if index >= 2 {
                let previous = roots
                    .iter()
                    .find(|root| root.command().role_id() == command.role_id())
                    .unwrap();
                assert_eq!(previous.command().node_id(), command.node_id());
                assert_eq!(command.task_ref().task_id().as_str(), "observe-both");
            }
            let now = domain::TimestampMs::new(2 + index as u64);
            let correlation = domain::CorrelationId::new("synthetic-live-multi-role").unwrap();
            for (sequence, phase) in [
                (1, integration::grpc::v0_4::ExecutionPhase::Started),
                (2, integration::grpc::v0_4::ExecutionPhase::Completed),
            ] {
                controller
                    .bridge
                    .consume(
                        integration::GrpcNodeEvent::NodeMessage {
                            node_id: command.node_id().to_string(),
                            session_id: "synthetic-session".into(),
                            message: integration::grpc::v0_4::NodeMessage {
                                message: Some(integration::grpc::v0_4::node_message::Message::ExecutionSnapshot(
                                    integration::grpc::v0_4::ExecutionSnapshot {
                                        session_id: "synthetic-session".into(),
                                        execution_id: attempt.execution_id().into(),
                                        last_sequence: sequence,
                                        phase: phase as i32,
                                        reason: "synthetic observed local outcome".into(),
                                    },
                                )),
                            },
                        },
                        now,
                        &correlation,
                    )
                    .unwrap();
                events.begin_batch().unwrap();
                apply_runtime_events(&mut controller, now, &correlation, &mut events.clone())
                    .unwrap();
                apply_runtime_outcomes(&mut controller, now, &correlation, &mut events.clone())
                    .unwrap();
                drive_ready_tasks(&mut controller, now, &correlation, &mut events.clone()).unwrap();
                events.commit_batch().unwrap();
            }
            assert_eq!(
                controller.bridge.attempt_history().len(),
                if index == 0 { 2 } else { 4 }
            );
            let trace = format!("{:?}", events.decoded_events().unwrap());
            assert_eq!(trace.matches("TaskSatisfied").count(), [1, 2, 2, 3][index]);
            assert_eq!(
                controller
                    .orchestrator
                    .execution(plan.goal().mission_id())
                    .unwrap()
                    .lifecycle()
                    == orchestration::MissionExecutionLifecycle::Completed,
                index == 3
            );
        }
    }

    /// A distance goal across floors leaves a second endpoint eligible without promising a route.
    #[test]
    fn cross_floor_goal_occupancy_keeps_the_pair_candidate() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("reset.json");
        let mut document = snapshot(&path, false);
        for record in document["records"].as_array_mut().expect("records") {
            if record["node_id"] == "node-b" && record["destination"] == "TARGET_any_targets|0" {
                record["goal_occupancy"] = serde_json::json!("any_at");
                record["goal_tolerance_m"] = serde_json::json!(2.0);
                record["status"] = serde_json::json!("unknown");
            }
        }
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("snapshot writes");
        let evidence = DeploymentFeasibility::load(&path).expect("goal evidence is valid");
        let group = domain::ExecutionGroupId::new("group").expect("group id valid");
        let pair = evidence
            .restrictions_for_plan(&plan(false, true), &group)
            .expect("both logical Actors have candidate endpoints");
        assert!(
            pair.iter()
                .any(|(_, nodes)| nodes.contains(&domain::NodeId::new("node-b").unwrap()))
        );
        document["records"][3]["status"] = serde_json::json!("incompatible");
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("contradictory snapshot writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("goal occupancy cannot justify an incompatibility")
                .contains("contradicts")
        );
    }

    /// A recomputed content digest cannot launder a decision that contradicts its facts.
    #[test]
    fn tampered_or_incomplete_reset_snapshot_fails_closed() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("reset.json");
        let mut document = snapshot(&path, false);
        document["records"][1]["status"] = serde_json::json!("compatible");
        std::fs::write(&path, document.to_string()).expect("tampered document writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("digest mismatch")
                .contains("digest")
        );
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("resealed document writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("decision mismatch")
                .contains("contradicts")
        );
        document = snapshot(&path, false);
        document["records"].as_array_mut().unwrap().pop();
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("truncated document writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("endpoint gap")
                .contains("coverage")
        );
        document = snapshot(&path, false);
        for record in document["records"].as_array_mut().unwrap() {
            if record["node_id"] == "node-b" {
                record["agent_id"] = serde_json::json!(0);
                record["profile"]["agent_id"] = serde_json::json!(0);
                record["start"]["position"] = serde_json::json!([0.0, 0.0, 0.0]);
            }
        }
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("duplicate-agent document writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("two Nodes cannot claim one reset agent")
                .contains("coverage")
        );
        document = snapshot(&path, false);
        document["initial_agent_positions"]["0"] = serde_json::json!(["zero", 0.0, 0.0]);
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("invalid position document writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("reset position must be numeric")
                .contains("initial positions")
        );
        document = snapshot(&path, false);
        document["records"][0]["profile"]["operation_support"]
            .as_object_mut()
            .unwrap()
            .remove("mobility.navigate@v1");
        seal(&mut document);
        std::fs::write(&path, document.to_string()).expect("missing capability writes");
        assert!(
            DeploymentFeasibility::load(&path)
                .expect_err("missing registered capability is not unknown")
                .contains("operation support")
        );
    }

    /// The real HTTP submission path rejects a fixed-world shortage atomically.
    #[tokio::test]
    async fn http_submission_rejects_pair_shortage_without_committing_mission() {
        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("reset.json");
        snapshot(&path, false);
        let feasibility = DeploymentFeasibility::load(&path).expect("snapshot is valid");
        assert_http_topology_rejection(
            &feasibility,
            plan_document(false, true),
            (409, "no feasible shared-world endpoint assignment"),
        )
        .await;
    }

    /// Check the actual HTTP admission boundary and its lack of durable acceptance mutations.
    async fn assert_http_topology_rejection(
        feasibility: &DeploymentFeasibility,
        document: serde_json::Value,
        expected: (u16, &str),
    ) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let directory = tempfile::tempdir().expect("temporary event directory");
        let event_log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
            .expect("event log opens");
        let verifier = document["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find_map(|task| {
                task["satisfaction"]
                    .get("verifier")
                    .filter(|value| value.is_object())
            });
        let feed = verifier.map(|spec| {
            let mut source = serde_json::json!({
                "schema_version":"roboguide.task-verifier-source/v0.1",
                "source_id":"synthetic-final-world", "source_revision": format!("sha256:{}", "a".repeat(64)),
                "identity":{"run_id":"synthetic", "episode_id":"synthetic", "scene_id":"synthetic",
                    "dataset_revision":"synthetic", "dataset_sha256":"a".repeat(64)},
                "verifier":spec["contract"], "supported_predicates":[spec["predicate"]], "verdict_finality":"terminal"
            });
            source["digest"] = serde_json::json!(format!("sha256:{:x}", Sha256::digest(serde_json::to_vec(&source).unwrap())));
            let source_path = directory.path().join("verifier-source.json");
            std::fs::write(&source_path, source.to_string()).unwrap();
            super::super::task_verifier::TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json")).unwrap()
        });
        let controller = Arc::new(Mutex::new(ControllerState {
            federation: Default::default(),
            bridge: IntegrationRuntimeBridge::new(
                control::ControlPlane::new(),
                state::InMemorySharedNodeState::new(),
                event_log.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator: MissionOrchestrator::new(),
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            memory_preparation: Default::default(),
            memory_use: Default::default(),
            memory_handoffs: BTreeMap::new(),
            runtime_revisions: Default::default(),
            hazards: application::hazards::HazardState::default(),
            safety_stops: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_artifact_store: None,
            verifier_source_digest: feed
                .as_ref()
                .map(|source| source.source_digest().to_string()),
        }));
        let gate = Arc::new(Mutex::new(()));
        let clock = runtime::SystemMonotonicClock::new();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("listener binds");
        let address = listener.local_addr().expect("listener has address");
        let body = document.to_string();
        let request = format!(
            "POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        let server = async {
            let (mut stream, _) = listener.accept().await.expect("request connects");
            handle_http_connection(
                &mut stream,
                &controller,
                &event_log,
                &gate,
                &clock,
                Some(feasibility),
                feed.as_ref(),
            )
            .await
            .expect("deployment rejection is an HTTP response");
        };
        let client = async {
            let mut stream = tokio::net::TcpStream::connect(address)
                .await
                .expect("client connects");
            stream
                .write_all(request.as_bytes())
                .await
                .expect("request writes");
            let mut response = String::new();
            stream
                .read_to_string(&mut response)
                .await
                .expect("response reads");
            assert!(
                response.starts_with(&format!("HTTP/1.1 {} ", expected.0)),
                "{response}"
            );
            assert!(response.contains(expected.1), "{response}");
        };
        tokio::time::timeout(Duration::from_secs(5), async {
            tokio::join!(server, client)
        })
        .await
        .expect("submission finishes");
        assert_eq!(event_log.latest_sequence().expect("sequence reads"), 0);
        assert!(
            event_log
                .load_checkpoint()
                .expect("checkpoint reads")
                .is_none()
        );
    }

    /// Freeze valid live endpoint evidence for topology bounds without live inventory or models.
    fn live_snapshot(path: &Path, second_goal_on_b: bool) -> serde_json::Value {
        let mut document = snapshot(path, second_goal_on_b);
        document["schema_version"] =
            serde_json::json!("roboguide.deployment-intent-feasibility/v0.5");
        document["execution_profile"] = serde_json::json!({
            "mode": "independent-live/v0.1", "registry_digest": format!("sha256:{}", "a".repeat(64)),
            "endpoints": [
                {"node_id": "node-a", "agent_id": 0, "operations": ["mobility.navigate@v1", "observation.verify@v1"]},
                {"node_id": "node-b", "agent_id": 1, "operations": ["mobility.navigate@v1"]}
            ]
        });
        seal(&mut document);
        std::fs::write(path, document.to_string()).unwrap();
        document
    }

    /// Freeze the producer's final-world barrier independently of goal syntax.
    fn finality_snapshot(path: &Path, second_goal_on_b: bool) {
        let mut document = live_snapshot(path, second_goal_on_b);
        document["schema_version"] =
            serde_json::json!("roboguide.deployment-intent-feasibility/v0.6");
        document["finality_profile"] = serde_json::json!({
            "verifier_publication": "after-all-session-slots",
            "endpoint_capacity": 1, "resource_release": "after-task-satisfaction"
        });
        seal(&mut document);
        std::fs::write(path, document.to_string()).unwrap();
    }

    /// Bind a final sink to an actual typed verifier requirement without changing execution.
    fn final_satisfaction(document: &mut serde_json::Value, index: usize) {
        document["tasks"][index]["satisfaction"] = serde_json::json!({
            "expected_effect": "joint terminal goal", "basis": "verifier-evidence",
            "verifier": {
                "contract": {"namespace":"observation","name":"verify","version":"v1"},
                "predicate":"joint-goal", "max_evidence_age_ms": 5000
            }
        });
    }

    /// Final verifier Tasks cannot retain more endpoints than the producer can close.
    #[tokio::test]
    async fn finality_rejects_retained_endpoint_cycles_before_admission() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("finality.json");
        finality_snapshot(&path, true);
        let evidence = DeploymentFeasibility::load(&path).unwrap();
        let group = domain::ExecutionGroupId::new("finality-group").unwrap();
        let mut document = plan_document(false, true);
        final_satisfaction(&mut document, 0);
        final_satisfaction(&mut document, 1);
        let plan = orchestration::decode_mission_plan(&document.to_string()).unwrap();
        assert!(evidence.restrictions_for_plan(&plan, &group).is_ok());
        // More total Tasks are fine when predecessor satisfaction releases
        // resources; only two final sinks retain slots at the publication barrier.
        let mut serial = plan_document(true, true);
        final_satisfaction(&mut serial, 1);
        let plan = orchestration::decode_mission_plan(&serial.to_string()).unwrap();
        assert!(evidence.restrictions_for_plan(&plan, &group).is_ok());
        let mut repeated = bounded_plan_document(4, false);
        for index in 0..4 {
            final_satisfaction(&mut repeated, index);
        }
        let plan = orchestration::decode_mission_plan(&repeated.to_string()).unwrap();
        assert!(
            evidence
                .restrictions_for_plan(&plan, &group)
                .unwrap_err()
                .contains("resource lifetime")
        );
        assert_http_topology_rejection(
            &evidence,
            repeated,
            (422, "Actor endpoint is needed by unordered work"),
        )
        .await;
        // Even two endpoints cannot close two retained slots on the same Actor.
        final_satisfaction(&mut serial, 0);
        let plan = orchestration::decode_mission_plan(&serial.to_string()).unwrap();
        assert!(evidence.restrictions_for_plan(&plan, &group).is_err());
        finality_snapshot(&path, false);
        let restricted = DeploymentFeasibility::load(&path).unwrap();
        let plan = orchestration::decode_mission_plan(&document.to_string()).unwrap();
        assert!(restricted.restrictions_for_plan(&plan, &group).is_err());
    }

    /// Produce complete logical slots with either sequential reuse or independent Actors.
    fn bounded_plan_document(count: usize, serial: bool) -> serde_json::Value {
        let mut document = plan_document(serial, true);
        let templates = document["tasks"].as_array().unwrap().clone();
        document["tasks"] = serde_json::Value::Array(
            (0..count)
                .map(|index| {
                    let mut task = templates[index % templates.len()].clone();
                    task["id"] = serde_json::json!(format!("bounded-task-{index}"));
                    task["depends_on"] = if serial && index > 0 {
                        serde_json::json!([format!("bounded-task-{}", index - 1)])
                    } else {
                        serde_json::json!([])
                    };
                    task
                })
                .collect(),
        );
        document
    }

    /// A serial topology cannot bypass the live ledger's maximum immutable Task coverage.
    #[tokio::test]
    async fn live_task_limit_rejects_33_serial_slots_before_http_acceptance() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("live.json");
        live_snapshot(&path, false);
        let evidence = DeploymentFeasibility::load(&path).unwrap();
        let group = domain::ExecutionGroupId::new("bounded-group").unwrap();
        for serial in [true, false] {
            let accepted =
                orchestration::decode_mission_plan(&bounded_plan_document(32, serial).to_string())
                    .unwrap();
            assert!(evidence.restrictions_for_plan(&accepted, &group).is_ok());
            let rejected =
                orchestration::decode_mission_plan(&bounded_plan_document(33, serial).to_string())
                    .unwrap();
            assert!(evidence.restrictions_for_plan(&rejected, &group).is_err());
        }
        assert_http_topology_rejection(
            &evidence,
            bounded_plan_document(33, true),
            (409, "no feasible shared-world endpoint assignment"),
        )
        .await;
        snapshot(&path, false);
        let legacy = DeploymentFeasibility::load(&path).unwrap();
        let legacy_serial =
            orchestration::decode_mission_plan(&bounded_plan_document(33, true).to_string())
                .unwrap();
        assert!(legacy.restrictions_for_plan(&legacy_serial, &group).is_ok());
    }

    /// Versioned opt-in topology keeps legacy barriers and supports sparse independent DAG work.
    #[test]
    fn live_profile_admits_sparse_tasks_without_changing_legacy_topology() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("live.json");
        live_snapshot(&path, false);
        let evidence = DeploymentFeasibility::load(&path).unwrap();
        let group = domain::ExecutionGroupId::new("group").unwrap();
        let pair = evidence
            .restrictions_for_plan(&plan(false, true), &group)
            .unwrap();
        assert_eq!(pair.len(), 2);
        assert!(
            pair.iter().all(
                |(_, nodes)| nodes == &BTreeSet::from([domain::NodeId::new("node-a").unwrap()])
            )
        );
        // This is candidate evidence only: actual resource scarcity remains Control-owned.
        let mut submitted = plan_document(false, true);
        let original = submitted["tasks"][1].clone();
        submitted["tasks"].as_array_mut().unwrap().push(original);
        submitted["tasks"][2]["id"] = serde_json::json!("followup-observation");
        submitted["tasks"][2]["depends_on"] = serde_json::json!([submitted["tasks"][1]["id"]]);
        let role = &mut submitted["tasks"][2]["roles"][0];
        role["execution_intent"]["operation"] =
            serde_json::json!({"namespace":"observation","name":"verify","version":"v1"});
        role["execution_intent"]["parameters"] =
            serde_json::json!({"expected":"detected(any_targets|0)"});
        role["requirements"]["capabilities"] = serde_json::json!([{"contract":{"namespace":"observation","name":"verify","version":"v1"},"constraints":[]}]);
        let decoded = orchestration::decode_mission_plan(&submitted.to_string()).unwrap();
        assert_eq!(
            evidence
                .restrictions_for_plan(&decoded, &group)
                .unwrap()
                .len(),
            2
        );
        submitted["tasks"][2]["roles"][0]["execution_intent"]["parameters"]["expected"] =
            serde_json::json!("detected(invented-object)");
        let decoded = orchestration::decode_mission_plan(&submitted.to_string()).unwrap();
        assert!(
            evidence
                .restrictions_for_plan(&decoded, &group)
                .unwrap_err()
                .contains("frozen world")
        );
        submitted["tasks"][2]["roles"][0]["execution_intent"]["parameters"]["expected"] =
            serde_json::json!("is_detected(any_targets|0)");
        let decoded = orchestration::decode_mission_plan(&submitted.to_string()).unwrap();
        assert!(
            evidence
                .restrictions_for_plan(&decoded, &group)
                .unwrap_err()
                .contains("read-only profile")
        );
    }
}
