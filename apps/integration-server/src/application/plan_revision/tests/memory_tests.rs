//! Original scoped declaration corruption/downgrade checks, with actual SQL and zero Node facts.

use super::*;
use ports::EventSink;

/// The new envelope retains explicit generation delegation and cannot masquerade as an older writer.
#[test]
fn v03_generation_receipt_is_explicit_and_fenced_on_restore() {
    let fixture = Fixture::new();
    fixture.install_for_trigger(true);
    fixture
        .command_schema(
            MEMORY_SCHEMA,
            "v03-generation",
            1,
            serde_json::json!({"kind":"authorize-generation"}),
            "administrator",
        )
        .unwrap();
    let live = fixture.controller.lock().unwrap();
    validate_restored(&live).unwrap();
    let mut saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&live).unwrap()).unwrap();
    saved.schema = PRE_REVISION_MEMORY_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(application::isolation::validate_isolation_checkpoint_schema(&saved).is_err());
}

/// Creates catalog metadata and permission only; no availability, execution or device fact is supplied.
fn declaration(fixture: &Fixture) -> serde_json::Value {
    fixture.install();
    fixture
        .command(
            "request-memory",
            1,
            serde_json::json!({"kind":"request-candidate","ticket_id":"memory-work"}),
            "proposer",
        )
        .unwrap();
    let payload = b"unit metadata declaration; availability remains unproven";
    std::fs::write(fixture.root.path().join("declared-source.bin"), payload).unwrap();
    let parent:domain::MemoryArtifactManifest=serde_json::from_value(serde_json::json!({
        "schema":"roboguide.memory-manifest/v0.1","selector":{"memory_id":"unit-memory","revision_id":"original"},
        "kind":"artifact","provider_id":"unit-provider","owner":{"owner":"robo_guide","component":"unit-metadata"},
        "scope":{"kind":"execution_group","execution_group_id":"original-unit-group"},"visibility":"exchangeable",
        "payload_schema":"unit-bytes/v1","media_type":"application/octet-stream",
        "artifact":{"content_digest":format!("sha256:{:x}",Sha256::digest(payload)),"byte_size":payload.len()},
        "source_mission_id":null,"source_execution_id":null,"source_task_ref":null,"created_at":1
    })).unwrap();
    let mut child = serde_json::to_value(&parent).unwrap();
    child["selector"]["revision_id"] = "child".into();
    child["scope"]["execution_group_id"] = "future-unit-group".into();
    let child: domain::MemoryArtifactManifest = serde_json::from_value(child).unwrap();
    let registry:domain::MemoryAuthorizationRegistry=serde_json::from_value(serde_json::json!({
        "schema_version":"roboguide.memory-authorization/v0.1","registry_id":"unit-memory-policy","revision":1,
        "policies":{
            "unit-memory/original":{"policy_id":"original-unit-policy","selector":parent.selector(),
                "manifest_digest":domain::memory_manifest_digest(&parent).unwrap(),"tenant_id":"unit-tenant",
                "principals":{"administrator":["handoff"]},"groups":[],"revoked":false},
            "unit-memory/child":{"policy_id":"child-unit-policy","selector":child.selector(),
                "manifest_digest":domain::memory_manifest_digest(&child).unwrap(),"tenant_id":"unit-tenant",
                "principals":{},"groups":[{"group_id":"future-unit-group","operations":["read","import"],
                    "providers":[{"provider_id":null,"local_system_id":null,"kind":"artifact","payload_schema":"unit-bytes/v1","media_type":"application/octet-stream"}]}],"revoked":false}
        }
    })).unwrap();
    fixture.transition(|controller| {
        controller
            .bridge
            .control_mut()
            .accept_memory_authorization_registry(registry, 0)
            .unwrap();
        controller.bridge.event_sink().clone().append(
            domain::TimestampMs::new(1),
            &domain::CorrelationId::new("unit-declaration").unwrap(),
            None,
            domain::EventPayload::MemoryManifestPublished {
                manifest: parent.clone(),
            },
        );
    });
    let catalog =
        artifact_http::ArtifactCatalog::replay_with_gate(&fixture.log, fixture.writer.clone())
            .unwrap();
    fixture
        .controller
        .lock()
        .unwrap()
        .memory_preparation
        .catalog = Some(catalog);
    serde_json::json!({"kind":"authorize-memory","ticket_id":"memory-work","authorization":{
        "parent_selector":parent.selector(),"target_group_id":"future-unit-group","child":child,
        "registry_id":"unit-memory-policy","registry_revision":1
    }})
}

/// A zero-event permission survives SQL serialization, stays unpublished and fences older wrappers.
#[test]
fn original_declaration_restore_does_not_publish_or_grant_old_writers() {
    let fixture = Fixture::new();
    let action = declaration(&fixture);
    let before = fixture.log.decoded_events().unwrap().len();
    let first = fixture
        .command_schema(
            MEMORY_SCHEMA,
            "declare-memory",
            2,
            action.clone(),
            "administrator",
        )
        .unwrap();
    assert_eq!(fixture.log.decoded_events().unwrap().len(), before);
    let original = fixture.controller.lock().unwrap().runtime_revisions.clone();
    assert_eq!(
        fixture
            .command_schema(MEMORY_SCHEMA, "declare-memory", 2, action, "administrator")
            .unwrap(),
        first
    );
    let live = fixture.controller.lock().unwrap();
    assert_eq!(
        serde_json::to_value(&live.runtime_revisions).unwrap(),
        serde_json::to_value(original).unwrap()
    );
    validate_restored(&live).unwrap();
    assert!(live.bridge.pending_dispatch_intents().is_empty());
    assert!(
        live.bridge
            .control()
            .group(&domain::ExecutionGroupId::new("future-unit-group").unwrap())
            .is_none()
    );
    let selector: domain::MemorySelector = serde_json::from_value(
        serde_json::json!({"memory_id":"unit-memory","revision_id":"child"}),
    )
    .unwrap();
    assert!(
        live.memory_preparation
            .catalog
            .as_ref()
            .unwrap()
            .memory_source(&selector)
            .unwrap()
            .is_none()
    );
    let mut restored: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&live).unwrap()).unwrap();
    application::isolation::validate_isolation_checkpoint_schema(&restored).unwrap();
    restored.schema = PRE_REVISION_MEMORY_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(
        application::isolation::validate_isolation_checkpoint_schema(&restored)
            .unwrap_err()
            .contains("scoped revision Memory")
    );
    let mut corrupt = live.clone();
    let mut state =
        serde_json::to_value(corrupt.runtime_revisions.values().next().unwrap()).unwrap();
    state["candidates"]["memory-work"]["memory"]["unit-memory/child"]["request"]["child"]["owner"] =
        serde_json::json!({"owner":"robo_guide","component":"different-owner"});
    corrupt.runtime_revisions.insert(
        fixture.plan.goal().mission_id().as_str().into(),
        serde_json::from_value(state).unwrap(),
    );
    assert!(validate_restored(&corrupt).is_err());
}

/// Neither operator role, old negotiation nor changed production metadata may stage a child.
#[test]
fn declaration_authority_and_complete_immutable_source_are_required() {
    let fixture = Fixture::new();
    let original = declaration(&fixture);
    assert!(
        fixture
            .command_schema(
                MEMORY_SCHEMA,
                "wrong-principal",
                2,
                original.clone(),
                "proposer"
            )
            .is_err()
    );
    assert!(
        fixture
            .command_schema(
                GENERATION_SCHEMA,
                "old-contract",
                2,
                original.clone(),
                "administrator"
            )
            .is_err()
    );
    for (field, value) in [
        (
            "owner",
            serde_json::json!({"owner":"robo_guide","component":"replacement"}),
        ),
        (
            "source_execution_id",
            serde_json::json!("invented-original-execution"),
        ),
        ("visibility", serde_json::json!("discoverable")),
        ("scope", serde_json::json!({"kind":"global"})),
    ] {
        let mut changed = original.clone();
        changed["authorization"]["child"][field] = value;
        assert!(
            fixture
                .command_schema(
                    MEMORY_SCHEMA,
                    &format!("changed-{field}"),
                    2,
                    changed,
                    "administrator"
                )
                .is_err()
        );
    }
    let live = fixture.controller.lock().unwrap();
    let state = live.runtime_revisions.values().next().unwrap();
    assert_eq!(state.revision, 2);
    assert!(state.candidates["memory-work"].memory.is_empty());
    validate_restored(&live).unwrap();
}
