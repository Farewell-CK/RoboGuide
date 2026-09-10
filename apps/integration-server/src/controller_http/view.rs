//! Read-only Controller HTTP inventory and State view projections.

use crate::*;
/// Projects current Shared Node State for Mission Intelligence without adding decision authority.
pub(crate) fn inventory_json(
    state: &state::InMemorySharedNodeState,
    observed_at: domain::TimestampMs,
) -> serde_json::Value {
    let nodes = state
        .snapshots()
        .into_iter()
        .map(|snapshot| {
            let registration = snapshot.registration();
            serde_json::json!({
                "node_id": snapshot.node_id().as_str(),
                "reported_health": format!("{:?}", snapshot.reported_status().health()),
                "source_observed_at_ms": snapshot.reported_status().observed_at().as_millis(),
                "received_at_ms": snapshot.reported_status_received_at().as_millis(),
                "liveness": format!("{:?}", snapshot.liveness().liveness()),
                "liveness_observed_at_ms": snapshot.liveness().observed_at().as_millis(),
                "capabilities": registration.capabilities().iter().map(|capability| {
                    serde_json::json!({
                        "kind": format!("{:?}", capability.kind()).to_ascii_lowercase(),
                        "available": capability.is_available(),
                    })
                }).collect::<Vec<_>>(),
                "contracts": registration.supported_contracts().iter()
                    .filter(|contract| registration.contract_is_available(contract))
                    .map(ToString::to_string)
                    .collect::<Vec<_>>(),
                "resources": registration.resources().iter().map(|resource| {
                    serde_json::json!({
                        "resource_id": resource.id().as_str(),
                        "kind": format!("{:?}", resource.kind()).to_ascii_lowercase(),
                        "capacity": resource.capacity(),
                    })
                }).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema_version": "roboguide.inventory/v0.1",
        "observed_at_ms": observed_at.as_millis(),
        "nodes": nodes,
    })
}

/// Describes built-in read adapters and selectively registered node providers.
pub(crate) fn state_providers_json(controller: &ControllerState) -> serde_json::Value {
    let built_in = [
        ("mission-orchestrator", "desired"),
        ("control-plane", "committed"),
        ("shared-node-state", "reported,observed"),
        ("runtime-orchestration", "derived"),
    ]
    .into_iter()
    .map(|(provider_id, semantics)| {
        serde_json::json!({
            "provider_id": provider_id,
            "owner": "roboguide",
            "semantics": semantics.split(',').collect::<Vec<_>>(),
            "writable_via_state_api": false,
        })
    })
    .collect::<Vec<_>>();
    let nodes = controller
        .bridge
        .state()
        .snapshots()
        .into_iter()
        .map(|snapshot| {
            let registration = snapshot.registration();
            serde_json::json!({
                "node_id": snapshot.node_id().as_str(),
                "state_exports": registration.state_exports(),
            })
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema": "roboguide.state-provider-catalog/v0.1",
        "built_in": built_in,
        "nodes": nodes,
        "belief_providers": [],
    })
}

/// Describes the generic catalog and selectively registered node Memory providers.
pub(crate) fn memory_providers_json(controller: &ControllerState) -> serde_json::Value {
    let nodes = controller
        .bridge
        .state()
        .snapshots()
        .into_iter()
        .map(|snapshot| {
            serde_json::json!({
                "node_id": snapshot.node_id().as_str(),
                "providers": snapshot.registration().memory_providers(),
            })
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema": "roboguide.memory-provider-catalog/v0.1",
        "built_in": [{
            "provider_id": "artifact-memory-catalog",
            "owner": "roboguide",
            "kinds": ["execution", "spatial", "semantic", "experience", "artifact"],
            "content_plane": "artifact-cas",
        }],
        "nodes": nodes,
    })
}

/// Builds a read-only federated State view over existing owners plus external records.
pub(crate) fn state_records_json(
    controller: &ControllerState,
    now: domain::TimestampMs,
    query: &std::collections::BTreeMap<&str, &str>,
) -> serde_json::Value {
    let mut records = Vec::new();
    for mission_id in controller.orchestrator.mission_ids() {
        let execution = controller
            .orchestrator
            .execution(&mission_id)
            .expect("Mission identity came from the same orchestrator");
        records.push(state_view_record(
            "roboguide",
            "mission",
            mission_id.as_str(),
            "desired",
            "roboguide:mission-orchestrator",
            "accepted-plan",
            serde_json::json!({
                "objective": execution.plan().goal().objective(),
                "task_ids": execution.plan().task_graph().tasks().iter()
                    .map(|task| task.task_id().as_str())
                    .collect::<Vec<_>>(),
                "schema": execution.plan().schema_version(),
            }),
            None,
        ));
        records.push(state_view_record(
            "roboguide",
            "mission",
            mission_id.as_str(),
            "derived",
            "roboguide:runtime-orchestration",
            "mission-lifecycle",
            serde_json::json!({"lifecycle": format!("{:?}", execution.lifecycle())}),
            None,
        ));
    }
    for group_id in controller.bridge.control().group_ids() {
        let Some(group) = controller.bridge.control().group(&group_id) else {
            continue;
        };
        records.push(state_view_record(
            "roboguide",
            "execution_group",
            group_id.as_str(),
            "committed",
            "roboguide:control-plane",
            "group-commitment",
            serde_json::json!({
                "mission_id": group.mission_id().as_str(),
                "lifecycle": format!("{:?}", group.lifecycle()),
                "assignments": group.assignments().iter().map(|assignment| serde_json::json!({
                    "role_id": assignment.role_id().as_str(),
                    "node_id": assignment.node_id().as_str(),
                    "resource_ids": assignment.resource_ids().iter()
                        .map(|resource| resource.as_str()).collect::<Vec<_>>(),
                })).chain(group.task_executions().flat_map(|task| task.assignments().iter().map(|assignment| serde_json::json!({
                    "task_id": task.task_ref().task_id().as_str(),
                    "role_id": assignment.role_id().as_str(),
                    "node_id": assignment.node_id().as_str(),
                    "resource_ids": assignment.resource_ids().iter()
                        .map(|resource| resource.as_str()).collect::<Vec<_>>(),
                })))).collect::<Vec<_>>(),
            }),
            None,
        ));
    }
    for snapshot in controller.bridge.state().snapshots() {
        records.push(state_view_record(
            "node",
            "node",
            snapshot.node_id().as_str(),
            "reported",
            &format!("node:{}/registration", snapshot.node_id()),
            "health",
            serde_json::json!({
                "health": format!("{:?}", snapshot.reported_status().health()).to_ascii_lowercase(),
                "source_observed_at_ms": snapshot.reported_status().observed_at().as_millis(),
                "received_at_ms": snapshot.reported_status_received_at().as_millis(),
            }),
            None,
        ));
        records.push(state_view_record(
            "node",
            "node",
            snapshot.node_id().as_str(),
            "observed",
            "roboguide:shared-node-state",
            "liveness",
            serde_json::json!({
                "liveness": format!("{:?}", snapshot.liveness().liveness()).to_ascii_lowercase(),
                "observed_at_ms": snapshot.liveness().observed_at().as_millis(),
            }),
            None,
        ));
    }
    for record in controller.bridge.state_records().records() {
        let key = record.key();
        records.push(state_view_record(
            &format!("{:?}", key.object().class()).to_ascii_lowercase(),
            key.object().object_type(),
            key.object().object_id(),
            &format!("{:?}", key.semantic()).to_ascii_lowercase(),
            &key.source().to_string(),
            key.channel_id(),
            serde_json::json!({
                "payload_schema": record.payload_schema(),
                "value": record.value(),
                "source_observed_at_ms": record.source_observed_at().map(domain::TimestampMs::as_millis),
                "received_at_ms": record.received_at().as_millis(),
                "valid_for_ms": record.valid_for_ms(),
                "confidence_millionths": record.confidence_millionths(),
                "source_epoch": record.source_epoch(),
                "sequence": record.sequence(),
            }),
            Some(record.is_stale_at(now)),
        ));
    }
    records.retain(|record| state_record_matches(record, query));
    serde_json::json!({
        "schema": "roboguide.state-query/v0.1",
        "observed_at_ms": now.as_millis(),
        "records": records,
    })
}

/// Creates one common read-facade envelope without moving authority into the API layer.
#[allow(clippy::too_many_arguments)]
pub(crate) fn state_view_record(
    object_class: &str,
    object_type: &str,
    object_id: &str,
    semantic: &str,
    source: &str,
    channel_id: &str,
    value: serde_json::Value,
    stale: Option<bool>,
) -> serde_json::Value {
    serde_json::json!({
        "object": {
            "class": object_class,
            "object_type": object_type,
            "object_id": object_id,
        },
        "semantic": semantic,
        "source": source,
        "channel_id": channel_id,
        "value": value,
        "stale": stale,
    })
}

/// Applies exact simple query filters while retaining stale records unless explicitly excluded.
pub(crate) fn state_record_matches(
    record: &serde_json::Value,
    query: &std::collections::BTreeMap<&str, &str>,
) -> bool {
    let object = &record["object"];
    let exact = [
        ("object_class", object["class"].as_str()),
        ("object_type", object["object_type"].as_str()),
        ("object_id", object["object_id"].as_str()),
        ("semantic", record["semantic"].as_str()),
        ("source", record["source"].as_str()),
        ("channel_id", record["channel_id"].as_str()),
    ];
    if exact.iter().any(|(name, actual)| {
        query
            .get(name)
            .is_some_and(|expected| Some(*expected) != *actual)
    }) {
        return false;
    }
    query
        .get("include_stale")
        .is_none_or(|include| *include != "false" || record["stale"].as_bool() != Some(true))
}
