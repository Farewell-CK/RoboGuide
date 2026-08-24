//! Deterministic offline tests for the HTTP reference adapter and wire contract.

use super::client::HttpTransport;
use super::*;
use domain::{
    CapabilityContractRef, CorrelationId, ExecutionCommand, ExecutionGroupId, ExecutionIntent,
    ExecutionValue, MissionId, NodeEvent, NodeHealth, RoleId, TaskId,
};
use ports::{NodeGateway, NodeGatewayErrorKind};
use serde_json::{Value, json};
use std::cell::RefCell;
use std::collections::{BTreeMap, VecDeque};
use std::rc::Rc;

/// Shared scripted transport state retained after the gateway owns the transport object.
#[derive(Default)]
struct ScriptState {
    /// Scripted GET outcomes in request order.
    gets: VecDeque<Result<String, HttpAdapterError>>,
    /// Scripted POST outcomes in request order.
    posts: VecDeque<Result<String, HttpAdapterError>>,
    /// JSON bodies posted by the gateway.
    posted_bodies: Vec<String>,
}

/// In-process HTTP transport replacement that performs no network access.
struct ScriptedTransport {
    /// Shared deterministic request and response state.
    state: Rc<RefCell<ScriptState>>,
}

impl HttpTransport for ScriptedTransport {
    /// Returns the next scripted GET result.
    fn get(&self, _url: &str) -> Result<String, HttpAdapterError> {
        self.state
            .borrow_mut()
            .gets
            .pop_front()
            .expect("scripted GET response must exist")
    }

    /// Records the request body and returns the next scripted POST result.
    fn post_json(&self, _url: &str, body: &str) -> Result<String, HttpAdapterError> {
        let mut state = self.state.borrow_mut();
        state.posted_bodies.push(body.to_string());
        state
            .posts
            .pop_front()
            .expect("scripted POST response must exist")
    }
}

/// Returns one valid versioned registration response.
fn registration_json(node_id: &str) -> String {
    json!({
        "schema_version": "roboguide.node.v0.1",
        "node_id": node_id,
        "local_runtime": {"name": "reference-eaios", "version": "1.0.0"},
        "capabilities": [{"kind": "transport", "available": true}],
        "resources": [{"id": "space-a", "kind": "space", "capacity": 1}]
    })
    .to_string()
}

/// Builds a gateway and retains its scripted transport state for assertions.
fn gateway(
    gets_after_registration: Vec<Result<String, HttpAdapterError>>,
    posts: Vec<Result<String, HttpAdapterError>>,
) -> (HttpNodeGateway, Rc<RefCell<ScriptState>>) {
    let state = Rc::new(RefCell::new(ScriptState {
        gets: std::iter::once(Ok(registration_json("node-a")))
            .chain(gets_after_registration)
            .collect(),
        posts: posts.into(),
        posted_bodies: vec![],
    }));
    let transport = Box::new(ScriptedTransport {
        state: Rc::clone(&state),
    });
    let gateway =
        HttpNodeGateway::connect_with_transport("http://reference.invalid".to_string(), transport)
            .expect("scripted registration must connect");
    (gateway, state)
}

/// Builds one canonical execution command for HTTP round-trip tests.
fn command() -> ExecutionCommand {
    command_for("node-a")
}

/// Builds one canonical execution command for a specified logical node.
fn command_for(node_id: &str) -> ExecutionCommand {
    let intent = ExecutionIntent::new(
        CapabilityContractRef::new("mobility", "move", "v1").expect("operation must be valid"),
        BTreeMap::from([
            (
                "destination".to_string(),
                ExecutionValue::String("zone-b".to_string()),
            ),
            ("speed".to_string(), ExecutionValue::Float(0.5)),
        ]),
    )
    .expect("intent must be valid");
    ExecutionCommand::new(
        MissionId::new("mission-a").expect("mission id must be valid"),
        TaskId::new("task-01").expect("task id must be valid"),
        ExecutionGroupId::new("group-a").expect("group id must be valid"),
        RoleId::new("transport").expect("role id must be valid"),
        domain::NodeId::new(node_id).expect("node id must be valid"),
        intent,
        CorrelationId::new("trace-a").expect("correlation id must be valid"),
    )
}

/// Registration conversion preserves identity, capability, resource, runtime, and contract data.
#[test]
fn http_registration_converts_to_domain() {
    let (gateway, _) = gateway(vec![], vec![]);

    assert_eq!(gateway.registration().node_id().as_str(), "node-a");
    assert_eq!(
        gateway.registration().local_runtime().name(),
        "reference-eaios"
    );
    assert_eq!(
        gateway.registration().contract_version().as_str(),
        "roboguide.node.v0.1"
    );
    assert_eq!(gateway.registration().capabilities().len(), 1);
    assert_eq!(gateway.registration().resources().len(), 1);
}

/// Status conversion retains source-local observation time and health.
#[test]
fn http_status_converts_to_domain() {
    let status = json!({
        "schema_version": "roboguide.node.v0.1",
        "node_id": "node-a",
        "health": "degraded",
        "source_observed_at_ms": 9000
    })
    .to_string();
    let (gateway, _) = gateway(vec![Ok(status)], vec![]);

    let status = gateway.status().expect("valid status must convert");

    assert_eq!(status.health(), NodeHealth::Degraded);
    assert_eq!(status.observed_at().as_millis(), 9000);
}

/// Execute serializes canonical intent and converts a matching completion observation.
#[test]
fn execute_intent_wire_round_trip_and_completion_conversion() {
    let response = json!({
        "schema_version": "roboguide.node.v0.1",
        "event": "task_completed",
        "node_id": "node-a",
        "task_ref": {"mission_id": "mission-a", "task_id": "task-01"},
        "group_id": "group-a",
        "role_id": "transport"
    })
    .to_string();
    let (mut gateway, state) = gateway(vec![], vec![Ok(response)]);
    let command = command();

    let event = gateway
        .execute(&command)
        .expect("matching completion must convert");

    assert!(matches!(event, NodeEvent::TaskCompleted { .. }));
    let request: Value = serde_json::from_str(&state.borrow().posted_bodies[0])
        .expect("captured execute request must be JSON");
    assert_eq!(request["schema_version"], "roboguide.node.v0.1");
    assert_eq!(
        request["intent"]["capability_contract"]["namespace"],
        "mobility"
    );
    assert_eq!(request["intent"]["capability_contract"]["name"], "move");
    assert_eq!(request["intent"]["parameters"]["destination"], "zone-b");
}

/// A task failure remains an execution observation rather than a transport error.
#[test]
fn task_failed_response_converts_to_node_event() {
    let response = json!({
        "schema_version": "roboguide.node.v0.1",
        "event": "task_failed",
        "node_id": "node-a",
        "task_ref": {"mission_id": "mission-a", "task_id": "task-01"},
        "group_id": "group-a",
        "role_id": "transport",
        "reason": "local planner rejected route"
    })
    .to_string();
    let (mut gateway, _) = gateway(vec![], vec![Ok(response)]);

    let event = gateway
        .execute(&command())
        .expect("task failure must convert");

    assert!(
        matches!(event, NodeEvent::TaskFailed { reason, .. } if reason == "local planner rejected route")
    );
}

/// Safe-stop observations preserve local safety authority without task-context fabrication.
#[test]
fn safe_stopped_response_converts_to_node_event() {
    let response = json!({
        "schema_version": "roboguide.node.v0.1",
        "event": "safe_stopped",
        "node_id": "node-a",
        "reason": "local obstacle interlock"
    })
    .to_string();
    let (mut gateway, _) = gateway(vec![], vec![Ok(response)]);

    let event = gateway.execute(&command()).expect("safe stop must convert");

    assert!(
        matches!(event, NodeEvent::SafeStopped { reason, .. } if reason == "local obstacle interlock")
    );
}

/// Commands targeting another NodeId are rejected before any HTTP invocation is sent.
#[test]
fn execute_node_id_mismatch_is_rejected() {
    let (mut gateway, state) = gateway(vec![], vec![]);

    let error = gateway
        .execute(&command_for("node-b"))
        .expect_err("wrong target node must be rejected");

    assert_eq!(error.kind(), NodeGatewayErrorKind::Protocol);
    assert!(state.borrow().posted_bodies.is_empty());
}

/// Unknown Node Contract versions fail during registration rather than silently degrading.
#[test]
fn schema_version_mismatch_is_rejected() {
    let state = Rc::new(RefCell::new(ScriptState {
        gets: VecDeque::from([Ok(
            registration_json("node-a").replace("roboguide.node.v0.1", "roboguide.node.v9")
        )]),
        ..ScriptState::default()
    }));
    let result = HttpNodeGateway::connect_with_transport(
        "http://reference.invalid".to_string(),
        Box::new(ScriptedTransport { state }),
    );

    assert!(matches!(result, Err(HttpAdapterError::Protocol { .. })));
}

/// Status from another logical node is rejected as a protocol identity violation.
#[test]
fn status_node_id_mismatch_is_rejected() {
    let status = json!({
        "schema_version": "roboguide.node.v0.1",
        "node_id": "node-b",
        "health": "online",
        "source_observed_at_ms": 10
    })
    .to_string();
    let (gateway, _) = gateway(vec![Ok(status)], vec![]);

    let error = gateway.status().expect_err("wrong node status must fail");

    assert_eq!(error.kind(), NodeGatewayErrorKind::Protocol);
}

/// Transport timeout remains a typed gateway failure and never becomes reported Offline.
#[test]
fn transport_timeout_is_preserved() {
    let timeout = HttpAdapterError::Transport {
        kind: NodeGatewayErrorKind::Timeout,
        reason: "scripted timeout".to_string(),
    };
    let (gateway, _) = gateway(vec![Err(timeout)], vec![]);

    let error = gateway.status().expect_err("timeout must remain visible");

    assert_eq!(error.kind(), NodeGatewayErrorKind::Timeout);
}

/// Structured JSON parameter payloads are rejected by the scalar v0.1 fixture decoder.
#[test]
fn malformed_parameter_payload_is_rejected() {
    let source = json!({
        "capability_contract": {"namespace": "mobility", "name": "move", "version": "v1"},
        "parameters": {"target": {"x": 1, "y": 2}}
    })
    .to_string();

    let result = decode_intent_fixture(&source);

    assert!(matches!(result, Err(HttpAdapterError::Protocol { .. })));
}
