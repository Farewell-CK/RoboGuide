//! Concrete reset/source schema decoding; no Control, Runtime or Task mutation APIs.

use super::deployment_document::{content_digest, valid_prefixed_sha256, valid_sha256};
use super::deployment_operation_admission::DeploymentOperationAdmission;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// Frozen, validated deployment observations, retaining exact provenance and default-off options.
#[derive(Debug, Clone)]
pub(super) struct FrozenFeasibilitySnapshot {
    /// Digest covering all source identity and endpoint restrictions.
    pub(super) digest: String,
    /// Exact-intent compatibility observations; unknown is never negative evidence.
    pub(super) entries: BTreeMap<(String, String), BTreeMap<domain::NodeId, String>>,
    /// Original run/episode/scene/dataset identity for cross-source checks.
    pub(super) identity: serde_json::Value,
    /// Original reset positions; never refreshed by loading or restore.
    pub(super) initial_positions: serde_json::Value,
    /// Exact configured Node-to-agent identity, retained only as source provenance.
    pub(super) node_agents: BTreeMap<domain::NodeId, i64>,
    /// Separately enabled canonical operation projection.
    pub(super) operation_admission: Option<DeploymentOperationAdmission>,
    /// Source-bound operation coverage for the explicit live profile.
    pub(super) live_operations: Option<BTreeMap<domain::NodeId, BTreeSet<String>>>,
    /// Explicit producer finality; verifier-backed slots retain endpoints until world closure.
    pub(super) terminal_verifier_barrier: bool,
}

impl FrozenFeasibilitySnapshot {
    /// Loads a bounded reset snapshot and checks its shape, digest, and decisions.
    pub(super) fn load(path: &Path) -> Result<Self, String> {
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
        let schema = body
            .get("schema_version")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default();
        let mut fields = BTreeSet::from([
            "schema_version",
            "authority",
            "identity",
            "initial_agent_positions",
            "records",
        ]);
        if schema == "roboguide.deployment-intent-feasibility/v0.4"
            || (matches!(
                schema,
                "roboguide.deployment-intent-feasibility/v0.5"
                    | "roboguide.deployment-intent-feasibility/v0.6"
            ) && body.contains_key("operation_admission"))
        {
            fields.insert("operation_admission");
        }
        if matches!(
            schema,
            "roboguide.deployment-intent-feasibility/v0.5"
                | "roboguide.deployment-intent-feasibility/v0.6"
        ) {
            fields.insert("execution_profile");
        }
        if schema == "roboguide.deployment-intent-feasibility/v0.6" {
            fields.insert("finality_profile");
        }
        if body.keys().map(String::as_str).collect::<BTreeSet<_>>() != fields
            || !matches!(
                schema,
                "roboguide.deployment-intent-feasibility/v0.3"
                    | "roboguide.deployment-intent-feasibility/v0.4"
                    | "roboguide.deployment-intent-feasibility/v0.5"
                    | "roboguide.deployment-intent-feasibility/v0.6"
            )
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
        let mut node_sources = BTreeMap::<domain::NodeId, String>::new();
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
            let source = profile["source_digest"]
                .as_str()
                .expect("digest checked above")
                .to_owned();
            if node_sources
                .insert(node_id.clone(), source.clone())
                .is_some_and(|prior| prior != source)
            {
                return Err("deployment feasibility changes one Node's registration source".into());
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
        let operation_admission = body
            .get("operation_admission")
            .map(|value| {
                DeploymentOperationAdmission::from_json(
                    value,
                    &node_agents,
                    &node_sources,
                    matches!(
                        schema,
                        "roboguide.deployment-intent-feasibility/v0.5"
                            | "roboguide.deployment-intent-feasibility/v0.6"
                    ),
                )
            })
            .transpose()?;
        let live_operations = body
            .get("execution_profile")
            .map(|profile| load_live_endpoints(profile, &node_agents))
            .transpose()?;
        let terminal_verifier_barrier = schema == "roboguide.deployment-intent-feasibility/v0.6";
        if terminal_verifier_barrier
            && body["finality_profile"]
                != serde_json::json!({
                    "verifier_publication": "after-all-session-slots",
                    "endpoint_capacity": 1,
                    "resource_release": "after-task-satisfaction"
                })
        {
            return Err("live deployment finality profile is unsupported".into());
        }
        Ok(Self {
            digest,
            entries,
            identity: body["identity"].clone(),
            initial_positions: body["initial_agent_positions"].clone(),
            node_agents,
            operation_admission,
            live_operations,
            terminal_verifier_barrier,
        })
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

/// Require an actual finite simulator position, never a null or string placeholder.
fn valid_position(value: &serde_json::Value) -> bool {
    value.as_array().is_some_and(|parts| {
        parts.len() == 3
            && parts
                .iter()
                .all(|part| part.as_f64().is_some_and(f64::is_finite))
    })
}

/// Validate one explicit endpoint profile against complete frozen source coverage.
fn load_live_endpoints(
    value: &serde_json::Value,
    node_agents: &BTreeMap<domain::NodeId, i64>,
) -> Result<BTreeMap<domain::NodeId, BTreeSet<String>>, String> {
    let profile = value
        .as_object()
        .ok_or("live execution profile is not an object")?;
    if profile.keys().map(String::as_str).collect::<BTreeSet<_>>()
        != BTreeSet::from(["mode", "registry_digest", "endpoints"])
        || profile["mode"] != "independent-live/v0.1"
        || !valid_prefixed_sha256(profile["registry_digest"].as_str().unwrap_or_default())
    {
        return Err("live execution profile identity is invalid".into());
    }
    let endpoints = profile["endpoints"]
        .as_array()
        .ok_or("live endpoints must be an array")?;
    if endpoints.is_empty() || endpoints.len() > 4 {
        return Err("live endpoint count is unsupported".into());
    }
    let mut result = BTreeMap::new();
    for endpoint in endpoints {
        let record = endpoint
            .as_object()
            .ok_or("live endpoint is not an object")?;
        if record.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from(["node_id", "agent_id", "operations"])
        {
            return Err("live endpoint fields are invalid".into());
        }
        let node = domain::NodeId::new(record["node_id"].as_str().unwrap_or_default())
            .map_err(|error| error.to_string())?;
        if record["agent_id"].as_i64() != node_agents.get(&node).copied() {
            return Err("live endpoint differs from frozen Node source".into());
        }
        let declared = record["operations"]
            .as_array()
            .ok_or("live operations must be an array")?;
        let mut operations = BTreeSet::new();
        for operation in declared {
            let name = operation.as_str().ok_or("live operation is not text")?;
            if !matches!(
                name,
                "mobility.move@v1"
                    | "mobility.navigate@v1"
                    | "object.relocate@v1"
                    | "observation.verify@v1"
                    | "observation.acquire@v1"
            ) || !operations.insert(name.to_owned())
            {
                return Err("live operation coverage is invalid".into());
            }
        }
        if operations.is_empty() || result.insert(node, operations).is_some() {
            return Err("live endpoint repeats an identity or lacks operations".into());
        }
    }
    if result.keys().cloned().collect::<BTreeSet<_>>() != node_agents.keys().cloned().collect() {
        return Err("live endpoints do not cover the frozen world".into());
    }
    Ok(result)
}

/// Validate the bounded neutral projection against the matrix's exact Node source coverage.
pub(super) fn load_operation_admission(
    document: &serde_json::Value,
    node_agents: &BTreeMap<domain::NodeId, i64>,
    node_sources: &BTreeMap<domain::NodeId, String>,
    allow_subset: bool,
) -> Result<DeploymentOperationAdmission, String> {
    let body = document
        .as_object()
        .ok_or("operation admission must be an object")?;
    if body.keys().map(String::as_str).collect::<BTreeSet<_>>()
        != BTreeSet::from([
            "schema_version",
            "operation",
            "parameter_names",
            "source_basis",
            "source_snapshot_digest",
            "registration_profile_digest",
            "object_sources",
            "destination_entities",
            "endpoint_profiles",
            "route_reachability",
        ])
        || body["schema_version"] != "roboguide.deployment-operation-admission/v0.1"
        || body["operation"] != "object.relocate@v1"
        || body["parameter_names"] != serde_json::json!(["destination", "object", "source"])
        || body["source_basis"] != "observed-initial-location"
        || body["route_reachability"] != "unknown"
        || ["source_snapshot_digest", "registration_profile_digest"]
            .iter()
            .any(|key| !valid_prefixed_sha256(body[*key].as_str().unwrap_or_default()))
    {
        return Err("operation admission schema, source or route claim is invalid".into());
    }
    let sources = body["object_sources"]
        .as_object()
        .ok_or("operation admission lacks object sources")?;
    let mut object_sources = BTreeMap::new();
    let mut source_ids = BTreeSet::new();
    if sources.is_empty() || sources.len() > 64 {
        return Err("operation admission object source coverage is invalid".into());
    }
    for (object, value) in sources {
        let source = value.as_str().unwrap_or_default();
        if object.trim().is_empty()
            || !source
                .strip_prefix("initial-location:")
                .is_some_and(valid_sha256)
            || !source_ids.insert(source.to_owned())
        {
            return Err(
                "operation admission source must identify one observed initial location".into(),
            );
        }
        object_sources.insert(object.clone(), source.to_owned());
    }
    let entities = body["destination_entities"]
        .as_array()
        .ok_or("operation admission lacks destination entities")?;
    let mut destinations = BTreeSet::new();
    if entities.is_empty() || entities.len() > 64 {
        return Err("operation admission destination coverage is invalid".into());
    }
    for value in entities {
        let destination = value.as_str().unwrap_or_default();
        if destination.trim().is_empty()
            || object_sources.contains_key(destination)
            || !destinations.insert(destination.to_owned())
        {
            return Err("operation admission destination entity is invalid".into());
        }
    }
    let profiles = body["endpoint_profiles"]
        .as_array()
        .ok_or("operation admission lacks endpoint profiles")?;
    let mut endpoints = BTreeSet::new();
    let max_endpoints = if allow_subset { 4 } else { 2 };
    if profiles.is_empty() || profiles.len() > max_endpoints {
        return Err("operation admission endpoint coverage is invalid".into());
    }
    for profile in profiles {
        let profile = profile
            .as_object()
            .ok_or("operation admission endpoint must be an object")?;
        if profile.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from([
                "agent_id",
                "node_id",
                "node_config_digest",
                "resource_kind",
                "resource_capacity",
            ])
            || profile["resource_kind"] != "space"
            || profile["resource_capacity"] != serde_json::json!(1)
        {
            return Err("operation admission needs a capacity-one space endpoint".into());
        }
        let node = domain::NodeId::new(profile["node_id"].as_str().unwrap_or_default())
            .map_err(|error| error.to_string())?;
        if profile["agent_id"].as_i64() != node_agents.get(&node).copied()
            || profile["node_config_digest"].as_str() != node_sources.get(&node).map(String::as_str)
            || !endpoints.insert(node)
        {
            return Err(
                "operation admission endpoint differs from the navigation Node source".into(),
            );
        }
    }
    if !allow_subset && endpoints != node_agents.keys().cloned().collect() {
        return Err("operation admission endpoint coverage is incomplete".into());
    }
    Ok(DeploymentOperationAdmission {
        object_sources,
        destinations,
        endpoints,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Rehashing cannot invent registered operation shapes or incomplete endpoint coverage.
    #[test]
    fn live_profile_rejects_invalid_endpoint_coverage() {
        let nodes = BTreeMap::from([
            (domain::NodeId::new("node-a").unwrap(), 0),
            (domain::NodeId::new("node-b").unwrap(), 1),
        ]);
        let valid = serde_json::json!({"mode":"independent-live/v0.1", "registry_digest":format!("sha256:{}", "a".repeat(64)), "endpoints":[
            {"node_id":"node-a", "agent_id":0,"operations":["mobility.move@v1"]},
            {"node_id":"node-b", "agent_id":1,"operations":["mobility.move@v1"]}
        ]});
        assert!(load_live_endpoints(&valid, &nodes).is_ok());
        for field in [
            "missing",
            "wrong-agent",
            "duplicate-operation",
            "unsupported",
        ] {
            let mut value = valid.clone();
            match field {
                "missing" => {
                    value["endpoints"].as_array_mut().unwrap().pop();
                }
                "wrong-agent" => value["endpoints"][1]["agent_id"] = serde_json::json!(0),
                "duplicate-operation" => {
                    value["endpoints"][1]["operations"] =
                        serde_json::json!(["mobility.move@v1", "mobility.move@v1"])
                }
                _ => {
                    value["endpoints"][1]["operations"] =
                        serde_json::json!(["invented.operation@v1"])
                }
            }
            assert!(load_live_endpoints(&value, &nodes).is_err(), "{field}");
        }
    }
}
