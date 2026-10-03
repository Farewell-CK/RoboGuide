//! Optional Control policy consuming neutral positive initial-operation costs.

use super::deployment_feasibility::{DeploymentFeasibility, content_digest, valid_prefixed_sha256};
use crate::*;
use control::InitialCandidatePreferences;
use ports::SharedNodeStateReader;
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;

/// Read and verify at most one MiB of content-addressed startup evidence.
fn load_body(path: &Path) -> Result<(serde_json::Value, String), String> {
    let mut raw = Vec::new();
    std::fs::File::open(path)
        .map_err(|error| error.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut raw)
        .map_err(|error| error.to_string())?;
    if raw.is_empty() || raw.len() > 1024 * 1024 {
        return Err("initial preference evidence is empty or exceeds 1 MiB".into());
    }
    let mut body: serde_json::Value =
        serde_json::from_slice(&raw).map_err(|error| error.to_string())?;
    let digest = body
        .as_object_mut()
        .ok_or("initial preference evidence must be an object")?
        .remove("digest")
        .and_then(|value| value.as_str().map(str::to_owned))
        .ok_or("initial preference evidence lacks its digest")?;
    if digest != content_digest(&body)? {
        return Err("initial preference digest differs from content".into());
    }
    Ok((body, digest))
}

/// Verify each projected cost against the original reset observation and source owner.
fn check_route_source(
    path: &Path,
    source_digest: &str,
    feasibility: &DeploymentFeasibility,
    costs: &BTreeMap<(String, String), BTreeMap<domain::NodeId, Option<u64>>>,
    disjoint: &BTreeSet<(String, String, domain::NodeId)>,
    geometry_enabled: bool,
) -> Result<(), String> {
    let (source, digest) = load_body(path)?;
    let identity = source["identity"]
        .as_object()
        .ok_or("route source lacks reset identity")?;
    if digest != source_digest
        || source["schema_version"]
            != if geometry_enabled {
                "roboguide.deployment-reset-route-support/v0.2"
            } else {
                "roboguide.deployment-reset-route-support/v0.1"
            }
        || source["purpose"] != "diagnostic_only"
        || source["authority"] != "deployment-observed-reset-state"
        || identity.get("preassignment_digest") != Some(&serde_json::json!(feasibility.digest()))
        || feasibility
            .identity
            .as_object()
            .expect("admitted identity")
            .iter()
            .any(|(key, value)| identity.get(key) != Some(value))
        || source["initial_agent_positions"] != feasibility.initial_positions
    {
        return Err("route cost source differs from the admitted reset world".into());
    }
    let records = source["records"]
        .as_array()
        .ok_or("route source lacks records")?;
    let available = match source["scope_status"].as_str() {
        Some("available") => true,
        Some("unavailable") if records.is_empty() => false,
        _ => return Err("route source scope is invalid".into()),
    };
    if records.len() > 128 {
        return Err("route source exceeds its record budget".into());
    }
    let mut observed = BTreeMap::new();
    let mut observed_disjoint = BTreeSet::new();
    for record in records {
        let node = domain::NodeId::new(record["node_id"].as_str().unwrap_or_default())
            .map_err(|error| error.to_string())?;
        let destination = record["destination"]
            .as_str()
            .filter(|value| !value.is_empty())
            .ok_or("route source destination is invalid")?;
        if record["agent_id"].as_i64() != feasibility.node_agents.get(&node).copied() {
            return Err("route source endpoint ownership differs".into());
        }
        let cost = match record["status"].as_str() {
            Some("supported") => {
                let length = record["selection"]["path_length_m"]
                    .as_f64()
                    .filter(|value| value.is_finite() && (0.0..=1_000_000.0).contains(value))
                    .ok_or("route source witness cost is invalid")?;
                Some((length * 1_000_000.0).round_ties_even() as u64)
            }
            Some("not_found" | "unavailable") if record["selection"].is_null() => None,
            _ => return Err("route source witness status is invalid".into()),
        };
        if geometry_enabled {
            let region = &record["region_analysis"];
            match region["status"].as_str() {
                Some("disjoint") => {
                    let lower_bound = region["orientation_safe_distance_lower_bound_m"]
                        .as_f64()
                        .filter(|value| value.is_finite())
                        .ok_or("route source disjoint bound is invalid")?;
                    let radius = record["official_robot_at_threshold_m"]
                        .as_f64()
                        .filter(|value| value.is_finite() && *value > 0.0)
                        .ok_or("route source disjoint radius is invalid")?;
                    let triangles = region["triangles"]
                        .as_u64()
                        .filter(|value| (1..=50_000).contains(value))
                        .ok_or("route source disjoint triangle count is invalid")?;
                    if cost.is_some()
                        || region["schema_version"] != "roboguide.static-navigation-region/v0.1"
                        || region["scope"] != "reset_static_start_component"
                        || region["reason_code"] != "static_component_disjoint"
                        || region["complete"] != true
                        || region["triangles_examined"].as_u64() != Some(triangles)
                        || lower_bound <= radius + 0.001
                    {
                        return Err(
                            "route source disjoint claim lacks a complete scoped miss".into()
                        );
                    }
                    observed_disjoint.insert((node.clone(), destination.to_string()));
                }
                Some("intersects" | "unknown") => {}
                _ => return Err("route source region status is invalid".into()),
            }
        }
        if observed
            .insert((node, destination.to_string()), cost)
            .is_some()
        {
            return Err("route source duplicates an endpoint/intent".into());
        }
    }
    let expected: BTreeSet<_> = costs
        .iter()
        .flat_map(|((_, destination), nodes)| {
            nodes.keys().map(|node| (node.clone(), destination.clone()))
        })
        .collect();
    let destinations: BTreeSet<_> = observed
        .keys()
        .map(|(_, destination)| destination)
        .collect();
    let expected_probe: BTreeSet<_> = feasibility
        .node_agents
        .keys()
        .flat_map(|node| {
            destinations
                .iter()
                .map(|destination| (node.clone(), (*destination).clone()))
        })
        .collect();
    if observed.keys().any(|key| !expected.contains(key))
        || (available && (observed.is_empty() || observed.keys().ne(expected_probe.iter())))
        || costs.iter().any(|((_, destination), nodes)| {
            nodes.iter().any(|(node, cost)| {
                observed
                    .get(&(node.clone(), destination.clone()))
                    .copied()
                    .flatten()
                    != *cost
            })
        })
    {
        return Err("projected costs differ from actual route-source coverage or witnesses".into());
    }
    for ((operation, destination), nodes) in costs {
        for node in nodes.keys() {
            if disjoint.contains(&(operation.clone(), destination.clone(), node.clone()))
                != observed_disjoint.contains(&(node.clone(), destination.clone()))
            {
                return Err("projected static support differs from actual region source".into());
            }
        }
    }
    Ok(())
}

/// Startup-bound optional costs, never an eligibility filter or binding map.
#[derive(Debug, Clone)]
pub(crate) struct InitialOperationPreferences {
    /// Content identity of the neutral source projection.
    digest: String,
    /// Exact operation/destination costs per declared deployment endpoint.
    costs: BTreeMap<(String, String), BTreeMap<domain::NodeId, Option<u64>>>,
    /// Scoped static misses affect order only and never remove eligible Nodes.
    disjoint: BTreeSet<(String, String, domain::NodeId)>,
    /// Local source receive time, unchanged by Mission arrival.
    received_at: domain::TimestampMs,
    /// Exclusive local expiry; checkpoint restore never renews it.
    expires_at: domain::TimestampMs,
    /// Only a fresh Controller process may consume the initial world.
    enabled: bool,
}

impl InitialOperationPreferences {
    /// Validates bounded neutral costs and their exact feasibility-source coverage.
    pub(crate) fn load(
        path: &Path,
        source_path: &Path,
        feasibility: &DeploymentFeasibility,
        received_at: domain::TimestampMs,
        fresh_controller: bool,
    ) -> Result<Self, String> {
        let (document, digest) = load_body(path)?;
        let body = document.as_object().expect("checked object");
        let geometry_enabled = body
            .get("schema_version")
            .and_then(serde_json::Value::as_str)
            == Some("roboguide.deployment-initial-operation-preferences/v0.2");
        if body.keys().map(String::as_str).collect::<BTreeSet<_>>()
            != BTreeSet::from([
                "schema_version",
                "authority",
                "scope",
                "source_digest",
                "feasibility_digest",
                "records",
            ])
            || (!geometry_enabled
                && body["schema_version"]
                    != "roboguide.deployment-initial-operation-preferences/v0.1")
            || body["authority"] != "deployment-observed-reset-state"
            || body["scope"] != "initial_world_before_first_dispatch"
            || body["feasibility_digest"] != feasibility.digest()
            || !valid_prefixed_sha256(body["source_digest"].as_str().unwrap_or_default())
        {
            return Err("initial operation preference schema, scope or source differs".into());
        }
        let records = body["records"]
            .as_array()
            .ok_or("initial costs must be an array")?;
        if records.is_empty() || records.len() > 128 {
            return Err("initial cost count exceeds its budget or is empty".into());
        }
        let mut costs = BTreeMap::<(String, String), BTreeMap<domain::NodeId, Option<u64>>>::new();
        let mut disjoint = BTreeSet::new();
        for record in records {
            let item = record.as_object().ok_or("initial cost must be an object")?;
            let mut expected_fields =
                BTreeSet::from(["operation", "parameters", "node_id", "cost_micrometers"]);
            if geometry_enabled {
                expected_fields.insert("static_support");
            }
            if item.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected_fields {
                return Err("initial cost fields are invalid".into());
            }
            let operation = item["operation"]
                .as_str()
                .filter(|value| !value.is_empty())
                .ok_or("initial cost operation is invalid")?;
            let parameters = item["parameters"]
                .as_object()
                .ok_or("initial cost parameters must be an object")?;
            let destination = parameters
                .get("destination")
                .and_then(serde_json::Value::as_str)
                .filter(|value| !value.is_empty())
                .ok_or("initial cost destination is invalid")?;
            if parameters.len() != 1 {
                return Err("initial cost parameters differ from this deployment profile".into());
            }
            let node = domain::NodeId::new(item["node_id"].as_str().unwrap_or_default())
                .map_err(|error| error.to_string())?;
            let cost = if item["cost_micrometers"].is_null() {
                None
            } else {
                Some(
                    item["cost_micrometers"]
                        .as_u64()
                        .filter(|cost| *cost <= 1_000_000_000_000)
                        .ok_or("initial cost is not a bounded nonnegative integer")?,
                )
            };
            if geometry_enabled {
                match (item["static_support"].as_str(), cost.is_some()) {
                    (Some("witnessed"), true) | (Some("unknown"), false) => {}
                    (Some("static-disjoint"), false) => {
                        disjoint.insert((operation.into(), destination.into(), node.clone()));
                    }
                    _ => return Err("initial static support and witnessed cost disagree".into()),
                }
            }
            if costs
                .entry((operation.into(), destination.into()))
                .or_default()
                .insert(node, cost)
                .is_some()
            {
                return Err("initial costs duplicate an exact operation/endpoint".into());
            }
        }
        if costs.len() != feasibility.entries.len()
            || costs.iter().any(|(intent, nodes)| {
                feasibility
                    .entries
                    .get(intent)
                    .is_none_or(|expected| nodes.keys().ne(expected.keys()))
            })
        {
            return Err("initial costs lack exact deployment intent/endpoint coverage".into());
        }
        check_route_source(
            source_path,
            body["source_digest"].as_str().expect("checked digest"),
            feasibility,
            &costs,
            &disjoint,
            geometry_enabled,
        )?;
        let expires_at = received_at
            .as_millis()
            .checked_add(control::MAX_INITIAL_PREFERENCE_AGE_MS)
            .map(domain::TimestampMs::new)
            .ok_or("initial preference lifetime overflows")?;
        Ok(Self {
            digest,
            costs,
            disjoint,
            received_at,
            expires_at,
            enabled: fresh_controller,
        })
    }

    /// Orders the first ready Task with bounded lookahead over the admitted profile.
    ///
    /// Missing static costs remain unknown. This policy cannot reserve the other
    /// Task's endpoint, overrule current Control eligibility, or promise a route.
    pub(crate) fn for_plan<S: SharedNodeStateReader, E: ports::EventSink>(
        &self,
        plan: &domain::MissionPlan,
        control: &control::ControlPlane,
        state: &S,
        now: domain::TimestampMs,
        correlation: &domain::CorrelationId,
        events: &mut E,
    ) -> Result<Option<InitialCandidatePreferences>, String> {
        if !self.enabled || now < self.received_at || now >= self.expires_at {
            return Ok(None);
        }
        let roots: Vec<_> = plan
            .task_graph()
            .tasks()
            .iter()
            .filter(|task| task.dependencies().is_empty())
            .collect();
        let Some(first) = roots.first() else {
            return Ok(None);
        };
        if roots.len() > 2
            || roots
                .iter()
                .any(|task| task.requirement().roles().len() != 1)
        {
            return Ok(None);
        }
        let mut options = Vec::new();
        for task in &roots {
            let role = &task.requirement().roles()[0];
            let intent = task
                .execution_intent(role.role_id())
                .ok_or("initial Task lacks exact intent")?;
            let destination = match intent.parameters().get("destination") {
                Some(domain::ExecutionValue::String(value)) if intent.parameters().len() == 1 => {
                    value
                }
                _ => return Ok(None),
            };
            let Some(costs) = self
                .costs
                .get(&(intent.operation().to_string(), destination.clone()))
            else {
                return Ok(None);
            };
            let candidates = match control.match_capabilities_for_mission(
                state,
                plan,
                task.requirement(),
                now,
                correlation,
                events,
            ) {
                Ok(candidates) => candidates,
                Err(_) => return Ok(None), // Ordinary preparation owns typed shortage/reconciliation handling.
            };
            let nodes = candidates
                .for_role(role.role_id())
                .expect("matched exact Role")
                .node_ids();
            if nodes.len() > 2 {
                return Ok(None);
            }
            options.push(
                nodes
                    .iter()
                    .map(|node| {
                        (
                            node.clone(),
                            costs.get(node).copied().flatten(),
                            self.disjoint.contains(&(
                                intent.operation().to_string(),
                                destination.clone(),
                                node.clone(),
                            )),
                        )
                    })
                    .collect::<Vec<_>>(),
            );
        }
        // Concurrent roots belonging to one Actor must retain its one endpoint.
        // The serial deployment's later roots never justify a distinct assignment.
        let separate = roots.len() == 2
            && roots[0].requirement().roles()[0].actor_id()
                != roots[1].requirement().roles()[0].actor_id();
        let mut scores = Vec::new();
        for (node, cost, is_disjoint) in &options[0] {
            let score = if separate {
                options[1]
                    .iter()
                    .filter(|(other, _, _)| other != node)
                    .map(|(_, other_cost, other_disjoint)| {
                        (
                            u8::from(*is_disjoint) + u8::from(*other_disjoint),
                            u8::from(cost.is_none()) + u8::from(other_cost.is_none()),
                            cost.unwrap_or(0) + other_cost.unwrap_or(0),
                        )
                    })
                    .min()
            } else {
                Some((
                    u8::from(*is_disjoint),
                    u8::from(cost.is_none()),
                    cost.unwrap_or(0),
                ))
            };
            if let Some(score) = score {
                scores.push((score, node.clone()));
            }
        }
        if scores.is_empty() {
            return Ok(None);
        }
        scores.sort();
        let ranks = scores
            .iter()
            .map(|(score, node)| {
                let rank = scores
                    .iter()
                    .position(|(other, _)| other == score)
                    .expect("own score exists") as u32;
                (node.clone(), rank)
            })
            .collect();
        let priorities =
            BTreeMap::from([(first.requirement().roles()[0].role_id().clone(), ranks)]);
        InitialCandidatePreferences::new(
            first.requirement().task_ref().clone(),
            priorities,
            self.digest.clone(),
            self.received_at,
            self.expires_at,
        )
        .map(Some)
        .map_err(|error| error.to_string())
    }
}
