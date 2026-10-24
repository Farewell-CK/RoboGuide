//! Adversarial metadata-only checks; positive process evidence lives in the real file profile.

use super::*;

/// Opens only empty authorities and SQLite, without introducing a Node or an execution fact.
fn empty(events: state::SqliteEventLog) -> ControllerState {
    ControllerState {
        mission_admissions: BTreeMap::new(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        memory_imports: BTreeMap::new(),
        memory_handoffs: BTreeMap::new(),
        runtime_revisions: Default::default(),
        safety_stops: BTreeMap::new(),
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            events,
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        verifier_seen: BTreeSet::new(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }
}

/// Supplies explicit original immutable metadata without claiming its bytes or Groups exist.
fn record() -> HandoffRecord {
    let parent:MemoryArtifactManifest=serde_json::from_value(serde_json::json!({"schema":"roboguide.memory-manifest/v0.1","selector":{"memory_id":"metadata","revision_id":"source"},"kind":"artifact","provider_id":"files","owner":{"owner":"robo_guide","component":"metadata-test"},"scope":{"kind":"execution_group","execution_group_id":"source"},"visibility":"exchangeable","payload_schema":"opaque/v1","media_type":"application/octet-stream","artifact":{"content_digest":format!("sha256:{}","a".repeat(64)),"byte_size":1},"source_mission_id":null,"source_execution_id":null,"source_task_ref":null,"created_at":1})).unwrap();
    let mut child = serde_json::to_value(&parent).unwrap();
    child["selector"]["revision_id"] = "child".into();
    child["scope"]["execution_group_id"] = "target".into();
    HandoffRecord {
        request: HandoffRequest {
            schema_version: "roboguide.memory-handoff/v0.1".into(),
            handoff_id: "original".into(),
            parent_selector: parent.selector().clone(),
            child: serde_json::from_value(child).unwrap(),
            typed_map: None,
            source_group_id: ExecutionGroupId::new("source").unwrap(),
            source_membership: GroupMembershipVersion {
                generation: 1,
                bindings_digest: format!("sha256:{}", "b".repeat(64)),
            },
            source_execution_ids: vec!["source-attempt".into()],
            target_membership: GroupMembershipVersion {
                generation: 1,
                bindings_digest: format!("sha256:{}", "c".repeat(64)),
            },
            registry_id: "policy".into(),
            registry_revision: 1,
            deadline_ms: 100,
        },
        parent,
        principal: "original-principal".into(),
        tenant_id: "original-tenant".into(),
        admitted_at_ms: 10,
        target_execution_ids: vec!["target-attempt".into()],
        attempts_digest: format!("sha256:{}", "d".repeat(64)),
        phase: HandoffPhase::Stopping,
        published_at_ms: None,
        receiver_proof_digest: None,
        receiver_proofs: BTreeMap::new(),
        committed_at_ms: None,
        reason: String::new(),
    }
}

/// Exact retries return only original history even after expiry; changed principal/tenant/time reject.
#[test]
fn stable_retry_cannot_replace_original_identity_or_deadline() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = empty(events);
    let original = record();
    controller
        .memory_handoffs
        .insert("original".into(), original.clone());
    assert_eq!(
        admit(
            &mut controller,
            original.request.clone(),
            "original-principal",
            Some("original-tenant"),
            domain::TimestampMs::new(101)
        )
        .unwrap(),
        "original"
    );
    for (principal, tenant) in [
        ("another", "original-tenant"),
        ("original-principal", "another"),
    ] {
        assert!(
            admit(
                &mut controller,
                original.request.clone(),
                principal,
                Some(tenant),
                domain::TimestampMs::new(11)
            )
            .is_err()
        );
    }
    let mut changed = original.request.clone();
    changed.deadline_ms += 1;
    assert!(
        admit(
            &mut controller,
            changed,
            "original-principal",
            Some("original-tenant"),
            domain::TimestampMs::new(11)
        )
        .is_err()
    );
    assert_eq!(controller.memory_handoffs["original"].admitted_at_ms, 10);
    assert_eq!(
        controller.memory_handoffs["original"].request.deadline_ms,
        100
    );
}

/// Empty evidence never establishes a stop barrier, and a manual target cannot gain dispatch.
#[test]
fn absent_stop_and_receiver_evidence_retain_both_fences() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = empty(events);
    let mut original = record();
    assert!(!source_stopped(&controller, &original));
    assert!(receiver_proof(&controller, &original, domain::TimestampMs::new(11)).is_err());
    original.phase = HandoffPhase::Manual;
    controller
        .memory_handoffs
        .insert("original".into(), original);
    for group in ["source", "target"] {
        assert!(holds_dispatch(
            &controller,
            &ExecutionGroupId::new(group).unwrap()
        ));
    }
    assert!(
        authorize_input(
            &controller,
            "target-attempt",
            &controller.memory_handoffs["original"]
                .request
                .child
                .selector()
                .clone(),
            domain::TimestampMs::new(11)
        )
        .is_err()
    );
    assert!(
        commit(
            &mut controller,
            "original",
            "original-principal",
            Some("original-tenant"),
            domain::TimestampMs::new(11)
        )
        .is_err()
    );
}

/// Ordinary readers see bounded lineage only for their original tenant, with a usable cursor.
#[test]
fn handoff_history_is_tenant_filtered_and_paginated() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = empty(events);
    controller
        .memory_handoffs
        .insert("original".into(), record());
    assert_eq!(
        view(&controller, Some("another"), None, 32)["records"],
        serde_json::json!([])
    );
    let visible = view(&controller, Some("original-tenant"), None, 1);
    assert_eq!(visible["records"].as_array().unwrap().len(), 1);
    assert_eq!(visible["next_after"], "original");
    assert_eq!(
        view(&controller, None, Some("original"), 1)["records"],
        serde_json::json!([])
    );
    assert_eq!(visible["records"][0]["source_barrier_complete"], false);
}

/// Old wrapper markers cannot hide a new release fence, including a zero-event revision.
#[test]
fn legacy_wrapper_cannot_carry_new_handoff_authority() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = empty(events);
    controller
        .memory_handoffs
        .insert("original".into(), record());
    let encoded = server_checkpoint_json(&controller).unwrap();
    let mut saved: ServerCheckpoint = serde_json::from_str(&encoded).unwrap();
    assert!(validate_isolation_checkpoint_schema(&saved).is_ok());
    saved.schema = PRE_MEMORY_HANDOFF_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(
        validate_isolation_checkpoint_schema(&saved)
            .unwrap_err()
            .contains("handoff authority")
    );
    assert!(validate_restored(&controller).is_err());
}

/// A retained first commit remains history after a later dispatch fence; retries cannot renew it.
#[test]
fn fenced_committed_retry_preserves_original_receipt_and_new_members_stay_fenced() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = empty(events);
    let mut original = record();
    original.phase = HandoffPhase::Manual;
    original.committed_at_ms = Some(50);
    original.reason = "post-commit delivery expired".into();
    controller
        .memory_handoffs
        .insert("original".into(), original.clone());
    assert_eq!(
        commit(
            &mut controller,
            "original",
            "original-principal",
            Some("original-tenant"),
            domain::TimestampMs::new(101)
        )
        .unwrap(),
        "original"
    );
    assert_eq!(
        controller.memory_handoffs["original"].committed_at_ms,
        Some(50)
    );
    assert_eq!(
        controller.memory_handoffs["original"].phase,
        HandoffPhase::Manual
    );
    assert!(!original_receivers_retired(&controller, &original));
    assert!(
        authorize_input(
            &controller,
            "replacement",
            original.request.child.selector(),
            domain::TimestampMs::new(11)
        )
        .is_err()
    );
}

/// A stale importing phase cannot authorize effects after either original Group authority is lost.
#[test]
fn in_flight_import_requires_current_source_and_original_budget_before_next_timer() {
    let directory = tempfile::tempdir().unwrap();
    let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = empty(events);
    let mut original = record();
    original.phase = HandoffPhase::Importing;
    original.published_at_ms = Some(11);
    controller
        .memory_handoffs
        .insert("original".into(), original.clone());
    let error = authorize_input(
        &controller,
        "target-attempt",
        original.request.child.selector(),
        domain::TimestampMs::new(12),
    )
    .unwrap_err();
    assert!(error.contains("policy unavailable"));
    let expired = authorize_input(
        &controller,
        "target-attempt",
        original.request.child.selector(),
        domain::TimestampMs::new(100),
    )
    .unwrap_err();
    assert!(expired.contains("deadline expired"));
    assert_eq!(
        controller.memory_handoffs["original"].phase,
        HandoffPhase::Importing
    );
}
