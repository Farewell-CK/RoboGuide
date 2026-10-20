//! Mission holds fence whole-Group continuation across timer, durable restore and explicit resume.

use super::*;

/// Commits a hold with the original attempts and recovery purpose through the real checkpoint store.
fn isolate(fixture: &Fixture) {
    let mut controller = fixture.controller.lock().unwrap();
    fixture.events.begin_batch().unwrap();
    controller
        .orchestrator
        .isolate_execution(
            &domain::MissionId::new("group-mission").unwrap(),
            orchestration::MissionIsolationSource::ReadyDispatch,
            TimestampMs::new(6),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    fixture
        .events
        .save_checkpoint(
            SERVER_CHECKPOINT_SCHEMA,
            &server_checkpoint_json(&controller).unwrap(),
        )
        .unwrap();
    fixture.events.commit_batch().unwrap();
}

/// Verifies that a hold retains exact current attempts, stop facts, resources and the original budget.
fn assert_held(fixture: &Fixture, ownership: &serde_json::Value) {
    let controller = fixture.controller.lock().unwrap();
    let mission = controller
        .orchestrator
        .execution(&domain::MissionId::new("group-mission").unwrap())
        .unwrap();
    assert_eq!(mission.isolation().unwrap().generation, 1);
    assert_eq!(
        mission.lifecycle(),
        orchestration::MissionExecutionLifecycle::Running
    );
    assert_eq!(controller.bridge.attempt_history().len(), 2);
    let recovery = controller.bridge.group_recovery(&fixture.group_id).unwrap();
    assert_eq!(recovery.deadline_ms, 105);
    assert!(recovery.replacements.is_empty());
    for attempt in &fixture.originals {
        assert_eq!(
            controller.bridge.current_task_attempt_id(
                &fixture.group_id,
                attempt.command().task_ref(),
                attempt.command().role_id(),
            ),
            Some(attempt.execution_id())
        );
        assert_eq!(
            controller.bridge.execution_status(attempt.execution_id()),
            Some(RemoteExecutionStatus::Cancelled)
        );
        assert_eq!(recovery.confirmed_at_ms[attempt.execution_id()], 7);
    }
    drop(controller);
    assert_eq!(&fixture.reservations(), ownership);
}

/// Restores durable authority and explicitly reacquires deployment/health rather than refreshing old evidence.
fn restore_and_register(fixture: &Fixture) {
    let registry = fixture
        .controller
        .lock()
        .unwrap()
        .bridge
        .control()
        .physical_entity_registry()
        .cloned()
        .unwrap();
    let checkpoint = fixture.events.load_checkpoint().unwrap().unwrap();
    let saved: ServerCheckpoint = serde_json::from_str(&checkpoint.checkpoint_json).unwrap();
    validate_isolation_checkpoint_schema(&saved).unwrap();
    let mut restored = ControllerState {
        bridge: IntegrationRuntimeBridge::restore_from_checkpoint(
            &saved.integration_json,
            fixture.events.clone(),
            integration::GrpcNodeRouter::default(),
            TimestampMs::new(10),
        )
        .unwrap(),
        orchestrator: MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap(),
        mission_admissions: saved.mission_admissions,
        memory_imports: saved.memory_imports,
        memory_preparation: saved.memory_preparation,
        memory_use: saved.memory_use,
        memory_handoffs: saved.memory_handoffs,
        runtime_revisions: saved.runtime_revisions,
        safety_stops: saved.safety_stops,
        verifier_seen: saved.verifier_seen,
        verifier_artifact_store: None,
        verifier_source_digest: saved.verifier_source_digest,
    };
    restored
        .bridge
        .control_mut()
        .install_physical_entity_registry(registry)
        .unwrap();
    let mut state = restored.bridge.state().clone();
    for index in 0..2 {
        restored
            .bridge
            .control_mut()
            .register_node(
                &mut state,
                registration(index, true, 2),
                domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(10)),
                TimestampMs::new(10),
                &fixture.correlation,
                &mut fixture.events.clone(),
            )
            .unwrap();
    }
    *restored.bridge.state_mut() = state;
    *fixture.controller.lock().unwrap() = restored;
}

/// A prior repeat authorization cannot bypass a hold; explicit resume retains the same deadline.
#[tokio::test]
async fn isolated_group_waits_for_explicit_resume_without_renewing_recovery_budget() {
    for resume_at in [10, 106] {
        let fixture = Fixture::new(2, true, false);
        fixture.authorize();
        let ownership = fixture.reservations();
        isolate(&fixture);
        for attempt in &fixture.originals {
            fixture.fact(attempt, 2, ExecutionPhase::Cancelled, 7);
        }
        for now in [8, 9] {
            fixture.tick(now).unwrap();
            assert_held(&fixture, &ownership);
        }
        let path = "/v1/missions/group-mission/resume";
        let (status, _) = fixture
            .request(
                "POST",
                path,
                serde_json::json!({"isolation_generation":2}),
                9,
            )
            .await;
        assert!(status.contains("409"));
        assert_held(&fixture, &ownership);
        let (status, _) = fixture
            .request(
                "POST",
                path,
                serde_json::json!({"isolation_generation":1}),
                resume_at,
            )
            .await;
        assert!(status.contains("200"), "{status}");
        fixture.tick(resume_at + 1).unwrap();
        let controller = fixture.controller.lock().unwrap();
        assert!(
            controller
                .orchestrator
                .execution(&domain::MissionId::new("group-mission").unwrap())
                .unwrap()
                .isolation()
                .is_none()
        );
        assert_eq!(
            controller.bridge.attempt_history().len(),
            if resume_at < 105 { 4 } else { 2 }
        );
        assert_eq!(
            controller
                .bridge
                .group_recovery(&fixture.group_id)
                .unwrap()
                .deadline_ms,
            105
        );
        drop(controller);
        assert_eq!(fixture.reservations(), ownership);
    }
}

/// A restored hold prevents preparation even after current registration makes continuation eligible.
#[tokio::test]
async fn restored_isolation_retains_group_stop_evidence_until_explicit_resume() {
    let fixture = Fixture::new(2, true, false);
    fixture.authorize();
    let ownership = fixture.reservations();
    isolate(&fixture);
    for attempt in &fixture.originals {
        fixture.fact(attempt, 2, ExecutionPhase::Cancelled, 7);
    }
    fixture.tick(8).unwrap();
    restore_and_register(&fixture);
    assert!(
        revalidate_mission_resume(
            &fixture.controller.lock().unwrap(),
            &domain::MissionId::new("group-mission").unwrap(),
            TimestampMs::new(11),
        )
        .is_ok()
    );
    fixture.tick(11).unwrap();
    assert_held(&fixture, &ownership);
    let (status, _) = fixture
        .request(
            "POST",
            "/v1/missions/group-mission/resume",
            serde_json::json!({"isolation_generation":1}),
            12,
        )
        .await;
    assert!(status.contains("200"), "{status}");
    fixture.tick(13).unwrap();
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        4
    );
    assert_eq!(fixture.reservations(), ownership);
}
