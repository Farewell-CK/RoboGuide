//! Actual Control allocations and public HTTP closure with synthetic task-owned observations.

use super::safety_stop::{admit, fixture_with_schema, observe, receipt};
use super::*;
use crate::application::safety_stop;
use integration::grpc::v0_4::ExecutionPhase;

/// Produces fixture-only native identities and original scope, without a hardware claim.
fn observation(fixture: &stop_recovery::Fixture) -> serde_json::Value {
    serde_json::json!({"execution_id":fixture.attempt.execution_id(),"execution_generation":1,
        "command_id":format!("cancel-{}",fixture.attempt.execution_id()),"terminal_state":"CANCELLED",
        "safe_state":true,"assurance":"no-effects","task_effects":{
            "native_session_id":"fixture-session","native_task_id":"fixture-task",
            "disposition":"no-effects","evidence_digest":format!("sha256:{}","a".repeat(64))}})
}

/// Builds an explicit command from the immutable recorded purpose, not a renewed stop window.
fn reconciliation(fixture: &stop_recovery::Fixture) -> serde_json::Value {
    serde_json::json!({"schema_version":"roboguide.stop-reconciliation/v0.1",
        "command_id":"operator-no-effects-1","execution_generation":1,"confirmed":true,
        "expected_record_digest":fixture.controller.safety_stops[fixture.attempt.execution_id()].record_digest().unwrap(),
        "disposition":"no-effects","attestation":"Fixture administrator independently checked original task dispatch history.",
        "evidence":{"native_task":"fixture-task","dispatches":0}})
}

/// Uses the same Mission cancellation transition that normal HTTP uses after admitting the stop.
fn request_cancel(fixture: &mut stop_recovery::Fixture) {
    let ControllerState {
        bridge,
        orchestrator,
        ..
    } = &mut fixture.controller;
    orchestrator
        .request_cancel(
            fixture.attempt.command().mission_id(),
            bridge.control_mut(),
            domain::TimestampMs::new(8),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
}

/// Original no-effects proof releases actual allocations without manufacturing device confirmation.
#[test]
fn original_no_effects_settles_cancellation_without_physical_confirmation() {
    let mut fixture = fixture_with_schema(domain::TASK_EFFECT_OBSERVATION_SCHEMA, "no-effects");
    admit(&mut fixture);
    receipt(&mut fixture);
    request_cancel(&mut fixture);
    fixture.fact(2, ExecutionPhase::Cancelled, 9);
    let payload = observation(&fixture);
    observe(
        &mut fixture,
        "s1",
        10,
        serde_json::json!({"observations":[payload]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(11)).unwrap();
    let mission = fixture.attempt.command().mission_id();
    assert!(!safety_stop::blocks_cancellation_release(
        &fixture.controller,
        mission
    ));
    assert!(safety_stop::blocks_release(&fixture.controller, mission));
    assert!(!safety_stop::attempt_confirmed(
        &fixture.controller,
        fixture.attempt.execution_id()
    ));
    application::apply_pending_cancellations(
        &mut fixture.controller,
        domain::TimestampMs::new(11),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(mission)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Cancelled
    );
    assert!(fixture.allocations().allocations().is_empty());
    assert!(!safety_stop::validate_restored(&fixture.controller).unwrap());
    let saved = serde_json::to_value(&fixture.controller.safety_stops).unwrap();
    assert_eq!(
        saved[fixture.attempt.execution_id()]["record"]["confirmed_at"],
        serde_json::Value::Null
    );
    let mut corrupt = saved.clone();
    corrupt[fixture.attempt.execution_id()]["settlement"]["basis"]["proof"]["observation"]["execution_generation"] =
        serde_json::json!(2);
    fixture.controller.safety_stops = serde_json::from_value(corrupt).unwrap();
    assert!(safety_stop::validate_restored(&fixture.controller).is_err());
    fixture.controller.safety_stops = serde_json::from_value(saved).unwrap();
    assert!(safety_stop::validate_restored(&fixture.controller).is_ok());
}

/// Incomplete, unrelated, late or device-labelled observations retain the original resource hold.
#[test]
fn no_effects_requires_complete_timely_original_evidence() {
    for case in [
        "generation",
        "command",
        "missing-witness",
        "device",
        "digest",
        "session",
        "late",
    ] {
        let mut fixture = fixture_with_schema(domain::TASK_EFFECT_OBSERVATION_SCHEMA, "no-effects");
        admit(&mut fixture);
        receipt(&mut fixture);
        request_cancel(&mut fixture);
        fixture.fact(2, ExecutionPhase::Cancelled, 9);
        let mut payload = observation(&fixture);
        match case {
            "generation" => payload["execution_generation"] = serde_json::json!(2),
            "command" => payload["command_id"] = serde_json::json!("other"),
            "missing-witness" => {
                payload.as_object_mut().unwrap().remove("task_effects");
            }
            "device" => payload["assurance"] = serde_json::json!("device-observation"),
            "digest" => payload["task_effects"]["evidence_digest"] = serde_json::json!("invalid"),
            _ => {}
        }
        let received = if case == "late" { 107 } else { 10 };
        observe(
            &mut fixture,
            if case == "session" { "old" } else { "s1" },
            received,
            serde_json::json!({"observations":[payload]}),
            None,
        );
        safety_stop::tick(
            &mut fixture.controller,
            domain::TimestampMs::new(received + 1),
        )
        .unwrap();
        assert!(
            safety_stop::blocks_release(
                &fixture.controller,
                fixture.attempt.command().mission_id()
            ),
            "{case}"
        );
        assert!(!fixture.allocations().allocations().is_empty(), "{case}");
    }
}

/// An authenticated manual decision preserves expired physical history and is exact-command idempotent.
#[test]
fn manual_settlement_preserves_original_record_and_refuses_conflicts() {
    let mut fixture = stop_recovery::Fixture::new();
    admit(&mut fixture);
    request_cancel(&mut fixture);
    fixture.fact(2, ExecutionPhase::Cancelled, 9);
    let id = fixture.attempt.execution_id().to_owned();
    let original =
        serde_json::to_value(&fixture.controller.safety_stops).unwrap()[&id]["record"].clone();
    let command = reconciliation(&fixture);
    let parsed =
        || safety_stop::ReconciliationRequest::parse(command.to_string().as_bytes()).unwrap();
    let first = safety_stop::reconcile_manual(
        &mut fixture.controller,
        &id,
        parsed(),
        "admin:fixture",
        domain::TimestampMs::new(1000),
    )
    .unwrap();
    assert_eq!(
        first,
        safety_stop::reconcile_manual(
            &mut fixture.controller,
            &id,
            parsed(),
            "admin:fixture",
            domain::TimestampMs::new(2000)
        )
        .unwrap()
    );
    assert!(
        safety_stop::reconcile_manual(
            &mut fixture.controller,
            &id,
            parsed(),
            "admin:other",
            domain::TimestampMs::new(2000)
        )
        .is_err()
    );
    assert_eq!(
        serde_json::to_value(&fixture.controller.safety_stops).unwrap()[&id]["record"],
        original
    );
    assert!(!safety_stop::attempt_confirmed(&fixture.controller, &id));
    application::apply_pending_cancellations(
        &mut fixture.controller,
        domain::TimestampMs::new(1001),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert!(fixture.allocations().allocations().is_empty());
    assert!(safety_stop::validate_restored(&fixture.controller).is_ok());
    let mut saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&fixture.controller).unwrap()).unwrap();
    let original_schema = saved.schema.clone();
    saved.schema = PRE_FEDERATION_EXECUTION_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(application::validate_isolation_checkpoint_schema(&saved).is_err());
    saved.schema = original_schema;
    fixture.controller.bridge = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        fixture.events.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(3000),
    )
    .unwrap();
    fixture.controller.orchestrator =
        MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap();
    fixture.controller.safety_stops = std::mem::take(&mut saved.safety_stops);
    assert!(safety_stop::validate_restored(&fixture.controller).is_ok());
    assert!(fixture.allocations().allocations().is_empty());
    let mut corrupt = serde_json::to_value(&fixture.controller.safety_stops).unwrap();
    corrupt[&id].as_object_mut().unwrap().remove("settlement");
    fixture.controller.safety_stops = serde_json::from_value(corrupt).unwrap();
    assert!(safety_stop::validate_restored(&fixture.controller).is_err());
}

/// Existing viewer/operator roles cannot gain reconciliation authority from a request header.
#[tokio::test]
async fn manual_settlement_public_route_is_authenticated_and_durable() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut fixture = stop_recovery::Fixture::new();
    admit(&mut fixture);
    request_cancel(&mut fixture);
    fixture.fact(2, ExecutionPhase::Cancelled, 9);
    let id = fixture.attempt.execution_id().to_owned();
    let body = reconciliation(&fixture).to_string();
    let controller = Arc::new(Mutex::new(fixture.controller.clone()));
    let gate = Arc::new(application::ApplicationWriter::default());
    let clock = runtime::FixedClock::new(domain::TimestampMs::new(1000));
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    fixture.events.begin_batch().unwrap();
    save_controller_checkpoint(&fixture.events, &before).unwrap();
    fixture.events.commit_batch().unwrap();
    let durable_before = fixture.events.load_checkpoint().unwrap();
    for case in ["operator", "tenant", "sql", "admin", "replay"] {
        let admin = case != "operator";
        if case == "sql" || case == "replay" {
            rusqlite::Connection::open(fixture.event_path()).unwrap().execute_batch("CREATE TRIGGER reject_settlement BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'injected settlement checkpoint failure'); END").unwrap();
        }
        let replay_checkpoint = if case == "replay" {
            fixture.events.load_checkpoint().unwrap()
        } else {
            None
        };
        let (mut server, mut client) = tokio::io::duplex(16384);
        let tenant_header = if case == "tenant" {
            "X-RoboGuide-Tenant: other-tenant\r\n"
        } else {
            ""
        };
        let request = format!(
            "POST /v1/executions/{id}/safety-stop/reconcile HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n{tenant_header}\r\n{body}",
            body.len()
        );
        let serve = async {
            let outcome = if admin {
                controller_http::handle_recovery_admin_test_connection(
                    &mut server,
                    &controller,
                    &fixture.events,
                    &gate,
                    &clock,
                    None,
                    None,
                )
                .await
            } else {
                controller_http::handle_http_connection(
                    &mut server,
                    &controller,
                    &fixture.events,
                    &gate,
                    &clock,
                    None,
                    None,
                )
                .await
            };
            if case == "sql" {
                assert!(outcome.is_err());
            } else {
                outcome.unwrap();
            }
            drop(server);
        };
        let exchange = async {
            client.write_all(request.as_bytes()).await.unwrap();
            let mut result = String::new();
            client.read_to_string(&mut result).await.unwrap();
            result
        };
        let result = tokio::time::timeout(Duration::from_secs(5), async {
            tokio::join!(serve, exchange).1
        })
        .await
        .unwrap();
        if case == "sql" {
            assert_eq!(
                server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
                before
            );
            assert_eq!(fixture.events.load_checkpoint().unwrap(), durable_before);
            rusqlite::Connection::open(fixture.event_path())
                .unwrap()
                .execute_batch("DROP TRIGGER reject_settlement")
                .unwrap();
            continue;
        }
        assert!(
            result.starts_with(if case == "admin" || case == "replay" {
                "HTTP/1.1 200"
            } else {
                "HTTP/1.1 403"
            }),
            "{result}"
        );
        if case == "operator" || case == "tenant" {
            assert_eq!(
                server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
                before
            );
            assert!(!fixture.allocations().allocations().is_empty());
        }
        if case == "replay" {
            assert_eq!(fixture.events.load_checkpoint().unwrap(), replay_checkpoint);
        }
    }
    fixture.controller = controller.lock().unwrap().clone();
    assert!(fixture.allocations().allocations().is_empty());
    let saved = fixture.events.load_checkpoint().unwrap().unwrap();
    assert!(saved.checkpoint_json.contains("test:recovery-admin"));
    assert!(
        saved
            .checkpoint_json
            .contains("roboguide.stop-settlement/v0.1")
    );
}

/// A task-local cancellation cannot clear a running Mission's physical continuation fence.
#[test]
fn no_effects_never_authorizes_running_mission_continuation() {
    let mut fixture = fixture_with_schema(domain::TASK_EFFECT_OBSERVATION_SCHEMA, "no-effects");
    admit(&mut fixture);
    receipt(&mut fixture);
    fixture.fact(2, ExecutionPhase::Cancelled, 9);
    let payload = observation(&fixture);
    observe(
        &mut fixture,
        "s1",
        10,
        serde_json::json!({"observations":[payload]}),
        None,
    );
    safety_stop::tick(&mut fixture.controller, domain::TimestampMs::new(11)).unwrap();
    let mission = fixture.attempt.command().mission_id();
    assert!(safety_stop::blocks_release(&fixture.controller, mission));
    assert!(safety_stop::blocks_cancellation_release(
        &fixture.controller,
        mission
    ));
    assert!(!fixture.allocations().allocations().is_empty());
}

/// Wrong scope, missing operator confirmation or forged identity fields cannot create a settlement.
#[test]
fn manual_settlement_refuses_incomplete_or_conflicting_original_evidence() {
    for case in [
        "generation",
        "record",
        "confirmation",
        "evidence",
        "forged-principal",
        "active",
    ] {
        let mut fixture = stop_recovery::Fixture::new();
        admit(&mut fixture);
        request_cancel(&mut fixture);
        if case != "active" {
            fixture.fact(2, ExecutionPhase::Cancelled, 9);
        }
        let before = server_checkpoint_json(&fixture.controller).unwrap();
        let mut command = reconciliation(&fixture);
        match case {
            "generation" => command["execution_generation"] = serde_json::json!(2),
            "record" => {
                command["expected_record_digest"] =
                    serde_json::json!(format!("sha256:{}", "b".repeat(64)))
            }
            "confirmation" => command["confirmed"] = serde_json::json!(false),
            "evidence" => command["evidence"] = serde_json::json!({}),
            "forged-principal" => command["principal"] = serde_json::json!("admin"),
            _ => {}
        }
        let result = safety_stop::ReconciliationRequest::parse(command.to_string().as_bytes())
            .and_then(|request| {
                safety_stop::reconcile_manual(
                    &mut fixture.controller,
                    fixture.attempt.execution_id(),
                    request,
                    "admin:fixture",
                    domain::TimestampMs::new(1000),
                )
            });
        assert!(result.is_err(), "{case}");
        assert_eq!(
            server_checkpoint_json(&fixture.controller).unwrap(),
            before,
            "{case}"
        );
    }
}
