//! Read-only support feedback for the first initial-world decision, never Node exclusion.

use super::deployment_feasibility::DeploymentFeasibility;
use super::initial_operation_preferences::InitialOperationPreferences;
use sha2::{Digest, Sha256};

/// Discards advisory Matching events rather than writing to the authoritative log.
struct AssessmentEvents;

impl ports::EventSink for AssessmentEvents {
    /// Ignores read-only query traces; no persistent event or checkpoint is created.
    fn append(
        &mut self,
        _: domain::TimestampMs,
        _: &domain::CorrelationId,
        _: Option<&domain::EventId>,
        _: domain::EventPayload,
    ) {
    }
}

/// Creates an unavailable assessment tied to the exact received plan bytes.
pub(crate) fn unavailable(
    plan: &domain::MissionPlan,
    request_body: &[u8],
    now: domain::TimestampMs,
    reason: &str,
) -> serde_json::Value {
    serde_json::json!({
        "schema_version": "roboguide.initial-operation-assessment/v0.2",
        "mission_id": plan.goal().mission_id().as_str(),
        "plan_body_sha256": format!("sha256:{:x}", Sha256::digest(request_body)),
        "scope": "initial_static_world",
        "decision": "unavailable",
        "reason_code": reason,
        "source_digest": null,
        "world_snapshot_digest": null,
        "local_how_digest": null,
        "received_at_ms": null,
        "expires_at_ms": null,
        "assessed_at_ms": now.as_millis(),
        "checked_combinations": 0,
        "roles": [],
        "candidate_diagnostics": [],
        "placement_failure": null,
    })
}

/// Computes bounded, advisory initial combinations using current Control eligibility.
///
/// A scoped miss is not permanent physical impossibility. Unknown and bounded
/// search misses keep every candidate. The deployment's one/two-endpoint initial
/// profile is inspected only before admission; later serial Tasks, restore and
/// recovery cannot reuse reset evidence as a fresh executability certificate.
#[allow(clippy::too_many_arguments)]
pub(super) fn assess<S: ports::SharedNodeStateReader>(
    deployment: &DeploymentFeasibility,
    source: Option<&InitialOperationPreferences>,
    plan: &domain::MissionPlan,
    control: &control::ControlPlane,
    state: &S,
    now: domain::TimestampMs,
    pristine: bool,
    request_body: &[u8],
) -> serde_json::Value {
    let mut result = unavailable(plan, request_body, now, "source_unconfigured");
    let Some(source) = source else { return result };
    result["source_digest"] = serde_json::json!(source.digest);
    result["world_snapshot_digest"] = serde_json::json!(deployment.digest());
    result["local_how_digest"] = source.route_source["identity"]["local_how_digest"].clone();
    result["received_at_ms"] = serde_json::json!(source.received_at.as_millis());
    result["expires_at_ms"] = serde_json::json!(source.expires_at.as_millis());
    if !source.enabled {
        result["reason_code"] = serde_json::json!("controller_restored");
        return result;
    }
    if !pristine {
        result["reason_code"] = serde_json::json!("world_already_admitted");
        return result;
    }
    if now < source.received_at || now >= source.expires_at {
        result["reason_code"] = serde_json::json!("source_expired");
        return result;
    }
    let group = domain::ExecutionGroupId::new(format!("group-{}", plan.goal().mission_id()))
        .expect("accepted Mission identity produces a valid group");
    let Ok(restrictions) = deployment.restrictions_for_plan(plan, &group) else {
        result["reason_code"] = serde_json::json!("deployment_placement_unavailable");
        result["placement_failure"] = serde_json::json!("deployment_contract_unavailable");
        return result;
    };
    // These existing restrictions are checked on a private Control copy only.
    let mut matching = control.clone();
    for (actor, nodes) in restrictions {
        if matching
            .set_actor_candidate_restriction(
                plan.goal().mission_id().clone(),
                actor,
                nodes,
                deployment.digest().to_string(),
            )
            .is_err()
        {
            result["reason_code"] = serde_json::json!("deployment_placement_unavailable");
            result["placement_failure"] = serde_json::json!("deployment_contract_unavailable");
            return result;
        }
    }
    let mut roots: Vec<_> = plan
        .task_graph()
        .tasks()
        .iter()
        .filter(|task| task.dependencies().is_empty())
        .collect();
    if roots.is_empty()
        || roots.len() > 2
        || roots
            .iter()
            .any(|task| task.requirement().roles().len() != 1)
    {
        result["reason_code"] = serde_json::json!("unsupported_plan_scope");
        return result;
    }
    if roots.len() == 2
        && roots[0].requirement().roles()[0].actor_id()
            == roots[1].requirement().roles()[0].actor_id()
    {
        // The serial profile must not use the initial start against a later Task.
        roots.truncate(1);
    }
    let correlation = domain::CorrelationId::new("initial-support-assessment")
        .expect("fixed query correlation is valid");
    let mut events = AssessmentEvents;
    let mut options = Vec::new();
    let mut roles = Vec::new();
    let mut diagnostics = Vec::new();
    for task in &roots {
        let Ok(reports) =
            matching.first_use_candidate_diagnostics(state, plan, task.requirement(), now)
        else {
            result["reason_code"] = serde_json::json!("unsupported_plan_scope");
            return result;
        };
        for report in reports {
            let mut report = serde_json::to_value(report).expect("flat Control counts serialize");
            report["task_id"] = serde_json::json!(task.requirement().task_ref().task_id().as_str());
            diagnostics.push(report);
        }
    }
    result["candidate_diagnostics"] = serde_json::json!(diagnostics);
    for task in &roots {
        let role = &task.requirement().roles()[0];
        let intent = task
            .execution_intent(role.role_id())
            .expect("accepted plan has an intent");
        let destination = match intent.parameters().get("destination") {
            Some(domain::ExecutionValue::String(value)) if intent.parameters().len() == 1 => value,
            _ => {
                result["reason_code"] = serde_json::json!("unsupported_plan_scope");
                return result;
            }
        };
        let key = (intent.operation().to_string(), destination.clone());
        let Some(costs) = source.costs.get(&key) else {
            result["reason_code"] = serde_json::json!("unsupported_plan_scope");
            return result;
        };
        let Ok(candidates) = matching.match_capabilities_for_mission(
            state,
            plan,
            task.requirement(),
            now,
            &correlation,
            &mut events,
        ) else {
            result["reason_code"] = serde_json::json!("current_candidates_unavailable");
            return result;
        };
        let nodes = candidates
            .for_role(role.role_id())
            .expect("exact role matched")
            .node_ids();
        if nodes.is_empty() {
            result["reason_code"] = serde_json::json!("current_candidates_unavailable");
            return result;
        }
        if nodes.len() > 2 {
            result["reason_code"] = serde_json::json!("unsupported_plan_scope");
            return result;
        }
        let mut counts = [0_u64; 4];
        let mut choices = Vec::new();
        for node in nodes {
            let disjoint = source
                .disjoint
                .contains(&(key.0.clone(), key.1.clone(), node.clone()));
            let observed = source.route_source["records"]
                .as_array()
                .and_then(|records| {
                    records.iter().find(|record| {
                        record["node_id"].as_str() == Some(node.as_str())
                            && record["destination"].as_str() == Some(destination.as_str())
                    })
                });
            let index = if disjoint {
                2
            } else if costs.get(node).copied().flatten().is_some() {
                0
            } else if observed.is_some_and(|record| record["status"] == "not_found") {
                1
            } else {
                3
            };
            counts[index] += 1;
            choices.push((node.clone(), disjoint));
        }
        roles.push(serde_json::json!({
            "task_id": task.requirement().task_ref().task_id().as_str(),
            "role_id": role.role_id().as_str(),
            "operation": intent.operation().to_string(),
            "witness_count": counts[0], "bounded_miss_count": counts[1],
            "scoped_disjoint_count": counts[2], "unknown_count": counts[3],
        }));
        options.push(choices);
    }
    let mut checked = 0;
    let mut viable = false;
    for (node, disjoint) in &options[0] {
        if options.len() == 1 {
            checked += 1;
            viable |= !disjoint;
        } else {
            for (other, other_disjoint) in &options[1] {
                // This distinction comes from the deployment session, not goal count.
                if node != other {
                    checked += 1;
                    viable |= !disjoint && !other_disjoint;
                }
            }
        }
    }
    result["roles"] = serde_json::json!(roles);
    result["checked_combinations"] = serde_json::json!(checked);
    if checked == 0 {
        result["reason_code"] = serde_json::json!("deployment_placement_unavailable");
        result["placement_failure"] = serde_json::json!("endpoint_cardinality");
    } else {
        result["decision"] = serde_json::json!(if viable { "not_blocked" } else { "blocked" });
        result["reason_code"] = serde_json::json!(if viable {
            "no_scoped_shortage"
        } else {
            "scoped_static_support_shortage"
        });
    }
    result
}
