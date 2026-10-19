//! Software contract tests with explicitly synthetic Node observations; these prove no device behavior.

use super::*;
use crate::application::safety_stop;
use integration::grpc::v0_4::{self as wire, node_message::Message as NodePayload};
use ports::SharedNodeStateReader;

/// Declares a test-only provider using the same registration and State export rules as deployment.
fn fixture() -> stop_recovery::Fixture {
    fixture_with_process_exit(false)
}

/// Builds either explicit v0.2 legacy or v0.3 original-process observation policy.
fn fixture_with_process_exit(process: bool) -> stop_recovery::Fixture {
    let mut fixture = stop_recovery::Fixture::new();
    let node = fixture.attempt.command().node_id().clone();
    let registration = fixture
        .controller
        .bridge
        .state()
        .node(&node)
        .unwrap()
        .registration();
    let owner = registration
        .operation_owner(fixture.attempt.command().intent().operation())
        .unwrap()
        .clone();
    let mut encoded = serde_json::to_value(registration).unwrap();
    encoded["local_systems"][0]["metadata"][domain::EXECUTION_SAFETY_METADATA_KEY] = serde_json::Value::String(serde_json::json!({
        "schema": "roboguide.execution-safety/v0.1", "operations": [{
            "operation": fixture.attempt.command().intent().operation(), "safe_state_export_id": "safe-process",
            "max_evidence_age_ms": 10, "stop_deadline_ms": 100, "min_confidence_millionths": if process { None } else { Some(800000) },
            "assurance": "software-process"
        }]
    }).to_string());
    let registration: domain::NodeRegistration = serde_json::from_value(encoded).unwrap();
    let export = domain::StateExportDescriptor::new(
        "safe-process",
        owner,
        domain::StateObjectRef::new(
            domain::StateObjectClass::Node,
            "process-safety",
            node.as_str(),
        )
        .unwrap(),
        domain::StateSemantic::Observed,
        if process {
            domain::PROCESS_EXIT_OBSERVATION_SCHEMA
        } else {
            domain::SAFE_STATE_OBSERVATION_SCHEMA
        },
        10,
    )
    .unwrap();
    let registration = registration
        .with_state_memory_exports(vec![export], Vec::new())
        .unwrap();
    let mut state = fixture.controller.bridge.state().clone();
    fixture
        .controller
        .bridge
        .control_mut()
        .register_node(
            &mut state,
            registration,
            domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(5)),
            domain::TimestampMs::new(5),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    *fixture.controller.bridge.state_mut() = state;
    fixture
        .controller
        .bridge
        .control_mut()
        .register_node_session(&node, "s1", 10_000)
        .unwrap();
    fixture
}

/// Starts the actual application hold and durable Runtime Cancel intent, without executing a device.
fn admit(fixture: &mut stop_recovery::Fixture) {
    let command = fixture.attempt.command().clone();
    safety_stop::admit(
        &mut fixture.controller,
        command.group_id(),
        command.task_ref(),
        command.role_id(),
        domain::TimestampMs::new(6),
        100,
    )
    .unwrap();
}

/// Supplies the already-admitted durable receipt to its exact application reducer.
fn receipt(fixture: &mut stop_recovery::Fixture) {
    let id = fixture.attempt.execution_id();
    safety_stop::receipt(
        &mut fixture.controller,
        fixture.attempt.command().node_id(),
        "s1",
        &wire::CommandReceipt {
            session_id: "s1".into(),
            command_id: format!("cancel-{id}"),
            execution_id: id.into(),
            kind: wire::CommandKind::CommandCancel as i32,
            sequence: 2,
            status: wire::CommandReceiptStatus::CommandPersisted as i32,
            reason: String::new(),
        },
        domain::TimestampMs::new(7),
        domain::TimestampMs::new(7),
    )
    .unwrap();
}

/// Builds an attempt-bound payload whose software assurance cannot be promoted to device evidence.
fn observation(fixture: &stop_recovery::Fixture) -> serde_json::Value {
    let command = fixture.attempt.command();
    serde_json::json!({
        "execution_id": fixture.attempt.execution_id(),
        "execution_generation": fixture.controller.bridge.current_attempt_generation(command.group_id(), command.task_ref(), command.role_id()).unwrap(),
        "command_id": format!("cancel-{}", fixture.attempt.execution_id()), "safe_state": true,
        "assurance": "software-process"
    })
}

/// Ingests the declared State batch through the maintained Node Protocol projection, with original time.
fn observe(
    fixture: &mut stop_recovery::Fixture,
    session: &str,
    received: u64,
    payload: serde_json::Value,
    confidence: Option<u32>,
) {
    fixture
        .controller
        .bridge
        .consume(
            integration::GrpcNodeEvent::NodeMessage {
                node_id: fixture.attempt.command().node_id().to_string(),
                session_id: session.into(),
                message: wire::NodeMessage {
                    message: Some(NodePayload::StateObservationBatch(
                        wire::StateObservationBatch {
                            session_id: session.into(),
                            sequence: 1,
                            observations: vec![wire::StateObservation {
                                export_id: "safe-process".into(),
                                json_value: serde_json::to_vec(&payload).unwrap(),
                                has_source_observed_at: true,
                                source_observed_at_ms: 999_999_999,
                                has_confidence: confidence.is_some(),
                                confidence_millionths: confidence.unwrap_or(0),
                            }],
                        },
                    )),
                },
            },
            domain::TimestampMs::new(received),
            &fixture.correlation,
        )
        .unwrap();
}

/// Reads the serialized authority phase without giving tests a second lifecycle map.
fn phase(fixture: &stop_recovery::Fixture) -> String {
    serde_json::to_value(&fixture.controller.safety_stops).unwrap()[fixture.attempt.execution_id()]
        ["record"]["phase"]
        .as_str()
        .unwrap()
        .into()
}

/// Requests hold ownership and fence both ordinary outcomes and explicit Mission continuation.
#[test]
fn stop_request_holds_mission_and_retains_allocation() {
    let mut fixture = fixture();
    let before = fixture.allocations();
    admit(&mut fixture);
    let mission = fixture.attempt.command().task_ref().mission_id();
    assert!(
        fixture
            .controller
            .orchestrator
            .execution(mission)
            .unwrap()
            .isolation()
            .is_some()
    );
    assert!(safety_stop::blocks_release(&fixture.controller, mission));
    assert!(
        application::revalidate_mission_resume(
            &fixture.controller,
            mission,
            domain::TimestampMs::new(8)
        )
        .is_err()
    );
    fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
    application::apply_runtime_outcomes(
        &mut fixture.controller,
        domain::TimestampMs::new(9),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(before, fixture.allocations());
    assert_eq!(phase(&fixture), "stop_requested");
}

/// A command receipt and a terminal Cancelled fact remain insufficient without safe-state evidence.
#[test]
fn accepted_cancel_is_not_stop_confirmation() {
    let mut fixture = fixture();
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(9)).unwrap();
    assert_eq!(phase(&fixture), "stop_command_accepted");
    assert!(safety_stop::blocks_release(
        &fixture.controller,
        fixture.attempt.command().task_ref().mission_id()
    ));
}

/// Exact timely evidence confirms software stopping and remains monotonic after the deadline.
#[test]
fn complete_software_stop_proof_survives_later_ticks_and_restore_validation() {
    let mut fixture = fixture();
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
    let observation = observation(&fixture);
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations": [observation]}),
        Some(900_000),
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "stop_confirmed");
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
    let original = serde_json::to_value(&fixture.controller.safety_stops).unwrap();
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(200)).unwrap();
    assert_eq!(
        original,
        serde_json::to_value(&fixture.controller.safety_stops).unwrap()
    );
    assert!(!safety_stop::blocks_release(
        &fixture.controller,
        fixture.attempt.command().task_ref().mission_id()
    ));
    let mut corrupt = original;
    corrupt[fixture.attempt.execution_id()]["safe_proof"]["observation"]["safe_state"] =
        serde_json::json!(false);
    fixture.controller.safety_stops = serde_json::from_value(corrupt).unwrap();
    assert!(safety_stop::validate_restored(&fixture.controller).is_err());
}

/// Source, attempt, confidence, assurance, duplicates and original receive TTL all fence confirmation.
#[test]
fn unsafe_or_stale_observations_cannot_confirm_stop() {
    for case in [
        "old-session",
        "old-generation",
        "wrong-command",
        "wrong-assurance",
        "duplicates",
        "low-confidence",
        "missing-confidence",
        "stale",
        "before-request",
    ] {
        let mut fixture = fixture();
        admit(&mut fixture);
        receipt(&mut fixture);
        fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
        let mut observation = observation(&fixture);
        match case {
            "old-generation" => observation["execution_generation"] = serde_json::json!(0),
            "wrong-command" => observation["command_id"] = serde_json::json!("different"),
            "wrong-assurance" => observation["assurance"] = serde_json::json!("device-observation"),
            _ => {}
        }
        let payload = if case == "duplicates" {
            serde_json::json!({"observations": [observation.clone(), observation]})
        } else {
            serde_json::json!({"observations": [observation]})
        };
        let confidence = match case {
            "low-confidence" => Some(799_999),
            "missing-confidence" => None,
            _ => Some(900_000),
        };
        observe(
            &mut fixture,
            if case == "old-session" { "s0" } else { "s1" },
            if case == "before-request" { 5 } else { 9 },
            payload,
            confidence,
        );
        safety_stop::tick(
            &mut fixture.controller,
            domain::TimestampMs::new(if case == "stale" { 19 } else { 10 }),
        )
        .unwrap();
        assert_ne!(phase(&fixture), "stop_confirmed", "{case}");
    }
}

/// An otherwise successful local terminal cannot substitute for a cancelled stop lifecycle.
#[test]
fn completed_operation_does_not_invent_stop_confirmation() {
    let mut fixture = fixture();
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Completed, 8);
    let observation = observation(&fixture);
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations": [observation]}),
        Some(900_000),
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "safe_state_observed");
}

/// Queueing cannot make a timely original receive late, while freshness still uses the decision clock.
#[test]
fn timely_fresh_proof_can_reduce_after_the_receive_deadline() {
    let mut fixture = fixture();
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
    let observation = observation(&fixture);
    observe(
        &mut fixture,
        "s1",
        105,
        serde_json::json!({"observations": [observation]}),
        Some(900_000),
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(108)).unwrap();
    assert_eq!(phase(&fixture), "stop_confirmed");
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
}

/// The production timer expires silent stops, checkpoints the fence and preserves ownership.
#[test]
fn silent_stop_escalates_in_the_actual_timer_transaction() {
    let mut fixture = fixture();
    admit(&mut fixture);
    let before = fixture.allocations();
    let controller = Arc::new(Mutex::new(fixture.controller.clone()));
    application::drive_application_timer(
        &controller,
        &fixture.events,
        &Arc::new(Mutex::new(())),
        domain::TimestampMs::new(106),
        None,
    )
    .unwrap();
    fixture.controller = controller.lock().unwrap().clone();
    assert_eq!(phase(&fixture), "manual_escalation");
    assert_eq!(before, fixture.allocations());
    let checkpoint = fixture.events.load_checkpoint().unwrap().unwrap();
    assert_eq!(
        checkpoint.schema,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
    );
    assert!(checkpoint.checkpoint_json.contains("manual_escalation"));
}

/// Legacy confirmation is retained as manual evidence and its missing hold is installed transactionally.
#[test]
fn legacy_stop_confirmation_requires_reconciliation_and_migrates_hold() {
    /// Reads the historical wrapper through the production migration hook.
    #[derive(serde::Deserialize)]
    struct LegacyWrapper {
        /// Uses the same version-aware deserializer as the composition checkpoint.
        #[serde(deserialize_with = "safety_stop::deserialize_purposes")]
        stops: BTreeMap<String, safety_stop::SafetyStopPurpose>,
    }
    let mut fixture = fixture();
    let command = fixture.attempt.command();
    let id = fixture.attempt.execution_id();
    let wrapper: LegacyWrapper = serde_json::from_value(serde_json::json!({"stops": {id: {
        "group_id": command.group_id(), "task_ref": command.task_ref(), "role_id": command.role_id(),
        "execution_id": id, "generation": 1, "controller_epoch": "legacy-epoch", "policy_digest": "legacy-unverified",
        "requested_at": 6, "deadline": 106, "phase": "stop_confirmed", "node_id": command.node_id(),
        "session_id": "s1", "stopped_at": 8, "safe_at": 9
    }}})).unwrap();
    fixture.controller.safety_stops = wrapper.stops;
    assert_eq!(phase(&fixture), "manual_escalation");
    assert!(safety_stop::validate_restored(&fixture.controller).unwrap());
    fixture.events.begin_batch().unwrap();
    safety_stop::restore_holds(&mut fixture.controller).unwrap();
    application::save_controller_checkpoint(
        &fixture.events,
        &server_checkpoint_json(&fixture.controller).unwrap(),
    )
    .unwrap();
    fixture.events.commit_batch().unwrap();
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
    assert!(safety_stop::blocks_release(
        &fixture.controller,
        fixture.attempt.command().task_ref().mission_id()
    ));
}

/// The public Mission cancellation route releases ownership only after the complete software proof.
#[tokio::test]
async fn confirmed_software_stop_allows_public_mission_cancellation() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut fixture = fixture();
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
    let observation = observation(&fixture);
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations": [observation]}),
        Some(900_000),
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    let controller = Arc::new(Mutex::new(fixture.controller.clone()));
    let gate = Arc::new(Mutex::new(()));
    let clock = runtime::FixedClock::new(domain::TimestampMs::new(10));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = async {
        let (mut stream, _) = listener.accept().await.unwrap();
        handle_http_connection(
            &mut stream,
            &controller,
            &fixture.events,
            &gate,
            &clock,
            None,
            None,
        )
        .await
        .unwrap();
    };
    let client = async {
        let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
        stream.write_all(b"POST /v1/missions/mission-recovery-driver/cancel HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n").await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        assert!(response.starts_with("HTTP/1.1 202 Accepted"));
        assert!(response.contains("Cancelled"));
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        tokio::join!(server, client);
    })
    .await
    .unwrap();
    fixture.controller = controller.lock().unwrap().clone();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempt.command().mission_id())
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Cancelled
    );
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .allocation_snapshot(domain::TimestampMs::new(10))
            .unwrap()
            .allocations()
            .is_empty()
    );
}

/// Produces explicit synthetic process-exit coverage, distinct from the execution store.
fn process_observation(fixture: &stop_recovery::Fixture, terminal: &str) -> serde_json::Value {
    let command = fixture.attempt.command();
    let generation = fixture
        .controller
        .bridge
        .current_attempt_generation(command.group_id(), command.task_ref(), command.role_id())
        .unwrap();
    serde_json::json!({
        "execution_id": fixture.attempt.execution_id(), "execution_generation": generation,
        "command_id": null, "safe_state": true, "assurance": "software-process",
        "terminal_state": terminal,
        "process_exit": { "instance_id":"synthetic-original-process", "pid":17, "exit_code":0,
            "group_id":command.group_id().as_str(),
            "attempts":[{"execution_id":fixture.attempt.execution_id(),"execution_generation":generation}]}
    })
}

/// Natural execution failure plus independent exit closes the safety hold without a fake Cancelled.
#[test]
fn original_worker_exit_closes_natural_failure_without_cancel_receipt() {
    let mut fixture = fixture_with_process_exit(true);
    fixture.fact(2, wire::ExecutionPhase::Failed, 5);
    admit(&mut fixture);
    let value = process_observation(&fixture, "FAILED");
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations":[value]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "stop_confirmed");
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_status(fixture.attempt.execution_id()),
        Some(orchestration::RemoteExecutionStatus::Failed)
    );
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
    application::apply_runtime_outcomes(
        &mut fixture.controller,
        domain::TimestampMs::new(10),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempt.command().mission_id())
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Failed
    );
}

/// A real receipt received before the natural failure must not prevent independent exit confirmation.
#[test]
fn process_exit_after_cancel_receipt_preserves_original_failed_result() {
    let mut fixture = fixture_with_process_exit(true);
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Failed, 8);
    let value = process_observation(&fixture, "FAILED");
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations":[value]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "stop_confirmed");
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
}

/// Process exit cannot stand in for actual command admission when the claimed lifecycle is Cancelled.
#[test]
fn cancelled_process_still_requires_the_real_durable_cancel_receipt() {
    let mut fixture = fixture_with_process_exit(true);
    admit(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Cancelled, 8);
    let value = process_observation(&fixture, "CANCELLED");
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations":[value]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "stop_requested");
    receipt(&mut fixture);
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "stop_confirmed");
}

/// Wrong process coverage, source or status cannot convert terminal flags into stop authority.
#[test]
fn process_exit_identity_coverage_status_and_schema_fail_closed() {
    for fault in [
        "group",
        "generation",
        "partial",
        "duplicate",
        "extra",
        "status",
        "source",
        "pid",
        "legacy",
    ] {
        let mut fixture = fixture_with_process_exit(fault != "legacy");
        fixture.fact(2, wire::ExecutionPhase::Failed, 5);
        admit(&mut fixture);
        let mut value = process_observation(&fixture, "FAILED");
        match fault {
            "group" => value["process_exit"]["group_id"] = serde_json::json!("other"),
            "generation" => {
                value["process_exit"]["attempts"][0]["execution_generation"] =
                    serde_json::json!(999)
            }
            "partial" => value["process_exit"]["attempts"] = serde_json::json!([]),
            "duplicate" => {
                let entry = value["process_exit"]["attempts"][0].clone();
                value["process_exit"]["attempts"]
                    .as_array_mut()
                    .unwrap()
                    .push(entry);
            }
            "extra" => value["process_exit"]["attempts"]
                .as_array_mut()
                .unwrap()
                .push(serde_json::json!({"execution_id":"not-current", "execution_generation":1})),
            "status" => value["terminal_state"] = serde_json::json!("COMPLETED"),
            "pid" => value["process_exit"]["pid"] = serde_json::json!(0),
            _ => {}
        }
        observe(
            &mut fixture,
            if fault == "source" { "old" } else { "s1" },
            9,
            serde_json::json!({"observations":[value]}),
            Some(900_000),
        );
        safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
        assert_ne!(phase(&fixture), "stop_confirmed", "{fault}");
    }
}

/// A naturally Completed local attempt can be stopped without becoming semantic success.
#[test]
fn completed_original_worker_exit_retains_the_mission_hold() {
    let mut fixture = fixture_with_process_exit(true);
    admit(&mut fixture);
    fixture.fact(2, wire::ExecutionPhase::Completed, 8);
    let value = process_observation(&fixture, "COMPLETED");
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations":[value]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    assert_eq!(phase(&fixture), "stop_confirmed");
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
    let mission = fixture
        .controller
        .orchestrator
        .execution(fixture.attempt.command().mission_id())
        .unwrap();
    assert!(mission.isolation().is_some());
    assert_eq!(
        mission.lifecycle(),
        orchestration::MissionExecutionLifecycle::Running
    );
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_status(fixture.attempt.execution_id()),
        Some(orchestration::RemoteExecutionStatus::Completed)
    );
}

/// Restoring a process proof must retain its exact original Group coverage and terminal state.
#[test]
fn process_stop_checkpoint_retains_original_frozen_coverage_and_status() {
    let mut fixture = fixture_with_process_exit(true);
    fixture.fact(2, wire::ExecutionPhase::Failed, 5);
    admit(&mut fixture);
    let value = process_observation(&fixture, "FAILED");
    observe(
        &mut fixture,
        "s1",
        9,
        serde_json::json!({"observations":[value]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(10)).unwrap();
    let original = serde_json::to_value(&fixture.controller.safety_stops).unwrap();
    for fault in ["missing-coverage", "wrong-coverage", "terminal"] {
        let mut corrupt = original.clone();
        let purpose = &mut corrupt[fixture.attempt.execution_id()];
        match fault {
            "missing-coverage" => {
                purpose.as_object_mut().unwrap().remove("process_attempts");
            }
            "wrong-coverage" => {
                purpose["process_attempts"][0]["execution_generation"] = serde_json::json!(999)
            }
            _ => {
                purpose["safe_proof"]["observation"]["terminal_state"] =
                    serde_json::json!("COMPLETED")
            }
        }
        fixture.controller.safety_stops = serde_json::from_value(corrupt).unwrap();
        assert!(
            safety_stop::validate_restored(&fixture.controller).is_err(),
            "{fault}"
        );
    }
    fixture.controller.safety_stops = serde_json::from_value(original).unwrap();
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
}

/// Explicit wrapper upgrade admits legacy evidence but cannot smuggle new process-stop authority.
#[test]
fn previous_wrapper_cannot_contain_process_stop_authority() {
    for process in [false, true] {
        let mut fixture = fixture_with_process_exit(process);
        admit(&mut fixture);
        let mut saved: ServerCheckpoint = serde_json::from_str(
            &application::server_checkpoint_json(&fixture.controller).unwrap(),
        )
        .unwrap();
        application::validate_isolation_checkpoint_schema(&saved).unwrap();
        saved.schema = PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA.into();
        assert_eq!(
            application::validate_isolation_checkpoint_schema(&saved).is_ok(),
            !process,
        );
    }
}
