//! Deployment-owned reset-state eligibility before Control commits a Mission.

use crate::*;
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
}

impl DeploymentFeasibility {
    /// Loads a bounded reset snapshot and checks its shape, digest, and decisions.
    pub(crate) fn load(path: &Path) -> Result<Self, String> {
        let raw = std::fs::read(path).map_err(|error| error.to_string())?;
        if raw.is_empty() || raw.len() > 1024 * 1024 {
            return Err("deployment feasibility artifact is empty or exceeds 1 MiB".into());
        }
        let mut document: serde_json::Value =
            serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
        let object = document
            .as_object_mut()
            .ok_or("deployment feasibility artifact must be an object")?;
        let digest = text(object.remove("digest"), "digest")?;
        if digest != content_digest(&document)? {
            return Err("deployment feasibility digest does not match content".into());
        }
        let body = document.as_object().expect("checked above");
        if body.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from([
                "schema_version",
                "authority",
                "identity",
                "initial_agent_positions",
                "records",
            ])
            || body["schema_version"] != "roboguide.deployment-intent-feasibility/v0.3"
            || body["authority"] != "deployment-observed-reset-state"
        {
            return Err("deployment feasibility schema or authority is unsupported".into());
        }
        let identity = body["identity"]
            .as_object()
            .ok_or("deployment feasibility identity must be an object")?;
        if identity.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from([
                "run_id",
                "episode_id",
                "scene_id",
                "dataset_revision",
                "dataset_sha256",
                "semantic_evidence_digest",
                "spatial_profile_digest",
                "habitat_seed",
                "episode_reset_count",
            ])
        {
            return Err("deployment feasibility identity fields are invalid".into());
        }
        for key in [
            "run_id",
            "episode_id",
            "scene_id",
            "dataset_revision",
            "dataset_sha256",
            "semantic_evidence_digest",
            "spatial_profile_digest",
        ] {
            if identity
                .get(key)
                .and_then(serde_json::Value::as_str)
                .is_none_or(str::is_empty)
            {
                return Err(format!("deployment feasibility identity lacks {key}"));
            }
        }
        if identity.get("episode_reset_count") != Some(&serde_json::json!(1)) {
            return Err("deployment feasibility must come from one reset".into());
        }
        if identity
            .get("habitat_seed")
            .and_then(serde_json::Value::as_i64)
            .is_none_or(|seed| seed < 0)
            || !valid_sha256(identity["dataset_sha256"].as_str().unwrap_or_default())
            || !valid_prefixed_sha256(
                identity["semantic_evidence_digest"]
                    .as_str()
                    .unwrap_or_default(),
            )
            || !valid_prefixed_sha256(
                identity["spatial_profile_digest"]
                    .as_str()
                    .unwrap_or_default(),
            )
        {
            return Err("deployment feasibility source identity is malformed".into());
        }
        let initial = body["initial_agent_positions"]
            .as_object()
            .ok_or("deployment feasibility lacks initial positions")?;
        if initial.is_empty()
            || initial
                .iter()
                .any(|(agent, position)| agent.parse::<i64>().is_err() || !valid_position(position))
        {
            return Err("deployment feasibility initial positions are invalid".into());
        }
        let records = body["records"]
            .as_array()
            .ok_or("deployment feasibility records must be an array")?;
        if records.is_empty() || records.len() > 4096 {
            return Err("deployment feasibility record count is invalid".into());
        }
        let mut entries = BTreeMap::<(String, String), BTreeMap<domain::NodeId, String>>::new();
        let mut node_agents = BTreeMap::<domain::NodeId, i64>::new();
        for record in records {
            let item = record
                .as_object()
                .ok_or("deployment feasibility record must be an object")?;
            if item.keys().map(String::as_str).collect::<BTreeSet<_>>()
                != BTreeSet::from([
                    "schema_version",
                    "node_id",
                    "agent_id",
                    "operation",
                    "destination",
                    "profile",
                    "goal_occupancy",
                    "goal_tolerance_m",
                    "start",
                    "destination_entity",
                    "status",
                    "reason",
                    "route_reachability_proven",
                ])
                || item["schema_version"] != "roboguide.habitat-spatial-feasibility/v0.2"
            {
                return Err("deployment feasibility record schema is invalid".into());
            }
            let node_id = domain::NodeId::new(text(item.get("node_id").cloned(), "node_id")?)
                .map_err(|error| error.to_string())?;
            let agent_id = item
                .get("agent_id")
                .and_then(serde_json::Value::as_i64)
                .filter(|agent| *agent >= 0)
                .ok_or("deployment feasibility agent id is invalid")?;
            if node_agents
                .insert(node_id.clone(), agent_id)
                .is_some_and(|prior| prior != agent_id)
            {
                return Err("deployment feasibility changes one Node's agent identity".into());
            }
            let profile = item
                .get("profile")
                .and_then(serde_json::Value::as_object)
                .ok_or("deployment feasibility record lacks registered profile")?;
            if profile.keys().map(String::as_str).collect::<BTreeSet<_>>()
                != BTreeSet::from(["agent_id", "node_id", "source_digest", "operation_support"])
                || !valid_prefixed_sha256(
                    profile
                        .get("source_digest")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or_default(),
                )
            {
                return Err("deployment feasibility registered profile is invalid".into());
            }
            if profile.get("agent_id") != Some(&serde_json::json!(agent_id))
                || profile.get("node_id") != Some(&serde_json::json!(node_id.as_str()))
            {
                return Err("deployment feasibility profile and endpoint differ".into());
            }
            let start = item
                .get("start")
                .and_then(serde_json::Value::as_object)
                .ok_or("deployment feasibility record lacks start")?;
            let destination_entity = item
                .get("destination_entity")
                .and_then(serde_json::Value::as_object)
                .ok_or("deployment feasibility record lacks destination entity")?;
            for location in [start, destination_entity] {
                if location.keys().map(String::as_str).collect::<BTreeSet<_>>()
                    != BTreeSet::from(["position", "region_id", "floor_id"])
                    || !location
                        .get("position")
                        .is_some_and(|value| value.is_null() || valid_position(value))
                    || ["region_id", "floor_id"].iter().any(|key| {
                        !location
                            .get(*key)
                            .is_some_and(|value| value.is_null() || value.as_str().is_some())
                    })
                {
                    return Err("deployment feasibility location fields are invalid".into());
                }
            }
            if start.get("position") != initial.get(&agent_id.to_string()) {
                return Err(
                    "deployment feasibility record differs from frozen reset position".into(),
                );
            }
            let operation = text(item.get("operation").cloned(), "operation")?;
            if !matches!(
                operation.as_str(),
                "mobility.move@v1" | "mobility.navigate@v1"
            ) {
                return Err("deployment feasibility operation is unsupported".into());
            }
            let destination = text(item.get("destination").cloned(), "destination")?;
            let goal_occupancy = text(item.get("goal_occupancy").cloned(), "goal_occupancy")?;
            if !matches!(
                goal_occupancy.as_str(),
                "none" | "any_at" | "other" | "unavailable"
            ) {
                return Err("deployment feasibility goal occupancy is invalid".into());
            }
            let goal_tolerance = item.get("goal_tolerance_m");
            if (goal_occupancy == "any_at"
                && !goal_tolerance
                    .and_then(serde_json::Value::as_f64)
                    .is_some_and(|value| value.is_finite() && value > 0.0))
                || (goal_occupancy != "any_at" && goal_tolerance != Some(&serde_json::Value::Null))
            {
                return Err("deployment feasibility goal tolerance is invalid".into());
            }
            let status = text(item.get("status").cloned(), "status")?;
            text(item.get("reason").cloned(), "reason")?;
            if !matches!(status.as_str(), "compatible" | "incompatible" | "unknown")
                || item.get("route_reachability_proven") != Some(&serde_json::Value::Bool(false))
            {
                return Err(
                    "deployment feasibility status or reachability claim is invalid".into(),
                );
            }
            let start_floor = start.get("floor_id").and_then(serde_json::Value::as_str);
            let destination_floor = destination_entity
                .get("floor_id")
                .and_then(serde_json::Value::as_str);
            let operation_support = profile
                .get("operation_support")
                .and_then(serde_json::Value::as_object)
                .ok_or("deployment feasibility profile lacks operation support")?;
            if operation_support
                .keys()
                .map(String::as_str)
                .collect::<BTreeSet<_>>()
                != BTreeSet::from(["mobility.move@v1", "mobility.navigate@v1"])
                || operation_support
                    .values()
                    .any(|value| !value.is_null() && !value.is_boolean())
            {
                return Err("deployment feasibility operation support is invalid".into());
            }
            let supports_transition = operation_support
                .get(&operation)
                .and_then(serde_json::Value::as_bool);
            let expected_status = match (start_floor, destination_floor, supports_transition) {
                (Some(start), Some(destination), _) if start == destination => "compatible",
                (Some(_), Some(_), Some(false)) if goal_occupancy == "none" => "incompatible",
                (Some(_), Some(_), Some(false)) => "unknown",
                (Some(_), Some(_), Some(true)) => "compatible",
                _ => "unknown",
            };
            if status != expected_status {
                return Err("deployment feasibility decision contradicts source facts".into());
            }
            if entries
                .entry((operation, destination))
                .or_default()
                .insert(node_id, status)
                .is_some()
            {
                return Err("deployment feasibility repeats an intent/Node observation".into());
            }
        }
        let nodes = node_agents.keys().cloned().collect::<BTreeSet<_>>();
        let agents = node_agents.values().copied().collect::<BTreeSet<_>>();
        if nodes.len() != initial.len()
            || agents.len() != nodes.len()
            || agents
                .iter()
                .map(ToString::to_string)
                .collect::<BTreeSet<_>>()
                != initial.keys().cloned().collect::<BTreeSet<_>>()
            || entries
                .values()
                .any(|entry| entry.keys().cloned().collect::<BTreeSet<_>>() != nodes)
        {
            return Err("deployment feasibility lacks complete endpoint coverage".into());
        }
        Ok(Self {
            digest,
            entries,
            identity: body["identity"].clone(),
            initial_positions: body["initial_agent_positions"].clone(),
            node_agents,
            initial_preferences: None,
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

    /// Derives candidate sets from exact accepted intents without choosing an assignment.
    pub(crate) fn restrictions_for_plan(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
    ) -> Result<Vec<(domain::ActorId, BTreeSet<domain::NodeId>)>, String> {
        let session = domain::ExecutionSessionDescriptor::from_plan(plan, group_id.clone())?
            .ok_or("deployment feasibility requires MissionPlan v0.8 session metadata")?;
        let mut candidates = BTreeMap::<domain::ActorId, BTreeSet<domain::NodeId>>::new();
        for task in plan.task_graph().tasks() {
            for role in task.requirement().roles() {
                if !role.resource_requirements().iter().any(|resource| {
                    resource.kind() == domain::ResourceKind::Space && resource.units() == 1
                }) {
                    return Err(
                        "shared-world deployment requires one exclusive space:1 slot per Task role"
                            .into(),
                    );
                }
                let actor = role
                    .actor_id()
                    .ok_or("deployment role has no logical Actor")?;
                let intent = task
                    .execution_intent(role.role_id())
                    .ok_or("deployment role lacks canonical intent")?;
                let destination = match (
                    intent.parameters().len(),
                    intent.parameters().get("destination"),
                ) {
                    (1, Some(domain::ExecutionValue::String(value))) if !value.is_empty() => value,
                    _ => {
                        return Err(
                            "deployment feasibility requires one exact destination parameter"
                                .into(),
                        );
                    }
                };
                let statuses = self
                    .entries
                    .get(&(intent.operation().to_string(), destination.clone()))
                    .ok_or("deployment feasibility has no record for an accepted intent")?;
                let allowed = statuses
                    .iter()
                    .filter(|(_, status)| status.as_str() != "incompatible")
                    .map(|(node, _)| node.clone())
                    .collect::<BTreeSet<_>>();
                candidates
                    .entry(actor.clone())
                    .and_modify(|existing| existing.retain(|node| allowed.contains(node)))
                    .or_insert(allowed);
            }
        }
        if candidates.values().any(BTreeSet::is_empty) {
            return Err("deployment feasibility leaves a logical Actor without a candidate".into());
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
        if !session.slots.iter().all(|slot| slot.independent)
            || (!supported_serial && !supported_pair)
        {
            return Err(
                "accepted-plan topology has no feasible shared-world endpoint assignment".into(),
            );
        }
        Ok(candidates.into_iter().collect())
    }

    /// Returns the immutable source digest for Control checkpoint attribution.
    pub(crate) fn digest(&self) -> &str {
        &self.digest
    }
}

/// Read one required nonblank JSON string without coercing other types.
fn text(value: Option<serde_json::Value>, field: &str) -> Result<String, String> {
    value
        .and_then(|value| {
            value
                .as_str()
                .filter(|text| !text.trim().is_empty())
                .map(str::to_owned)
        })
        .ok_or_else(|| format!("deployment feasibility {field} must be nonblank text"))
}

/// Check one canonical lowercase SHA-256 digest without a prefix.
fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

/// Check the digest syntax used for source artifacts and Control attribution.
pub(super) fn valid_prefixed_sha256(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(valid_sha256)
}

/// Require an actual finite simulator position, never a null or string placeholder.
fn valid_position(value: &serde_json::Value) -> bool {
    value.as_array().is_some_and(|parts| {
        parts.len() == 3
            && parts
                .iter()
                .all(|part| part.as_f64().is_some_and(f64::is_finite))
    })
}

/// Replace parsed floating values by exact IEEE-754 bits before hashing.
fn canonical_digest_value(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Number(number) if number.is_f64() => {
            let bits = number.as_f64().expect("floating number has f64").to_bits();
            *value = serde_json::json!({"$f64_bits": format!("{bits:016x}")});
        }
        serde_json::Value::Array(values) => {
            for item in values {
                canonical_digest_value(item);
            }
        }
        serde_json::Value::Object(values) => {
            for item in values.values_mut() {
                canonical_digest_value(item);
            }
        }
        _ => {}
    }
}

/// Compute the v0.3 language-neutral content identity of an evidence body.
pub(super) fn content_digest(body: &serde_json::Value) -> Result<String, String> {
    let mut canonical = body.clone();
    canonical_digest_value(&mut canonical);
    let encoded = serde_json::to_vec(&canonical).map_err(|error| error.to_string())?;
    Ok(format!("sha256:{:x}", Sha256::digest(encoded)))
}

#[cfg(test)]
mod tests {
    use super::*;

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

    /// Register current capability and resource facts for initial policy tests.
    fn initial_cost_nodes(
        control: &mut control::ControlPlane,
        plan: &domain::MissionPlan,
        events: &mut state::SqliteEventLog,
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
                    domain::NodeStatus::new(
                        domain::NodeHealth::Online,
                        domain::TimestampMs::new(0),
                    ),
                    domain::TimestampMs::new(0),
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
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let directory = tempfile::tempdir().expect("temporary directory");
        let path = directory.path().join("reset.json");
        snapshot(&path, false);
        let feasibility = DeploymentFeasibility::load(&path).expect("snapshot is valid");
        let event_log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
            .expect("event log opens");
        let controller = Arc::new(Mutex::new(ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control::ControlPlane::new(),
                state::InMemorySharedNodeState::new(),
                event_log.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator: MissionOrchestrator::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: None,
        }));
        let gate = Arc::new(Mutex::new(()));
        let clock = runtime::SystemMonotonicClock::new();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("listener binds");
        let address = listener.local_addr().expect("listener has address");
        let body = plan_document(false, true).to_string();
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
                Some(&feasibility),
                None,
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
            assert!(response.starts_with("HTTP/1.1 409 Conflict"));
            assert!(response.contains("no feasible shared-world endpoint assignment"));
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
}
