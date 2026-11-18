//! Explicit adoption uses the original writer, authorization and transport projection boundaries.

use super::*;

/// Creates a fixed explicit original map and complete ownership, without any Node registration.
fn adoption_command() -> domain::NodeIdentityAdoptionCommand {
    domain::NodeIdentityAdoptionCommand {
        schema: domain::NODE_IDENTITY_ADOPTION_SCHEMA.into(),
        command_id: "adopt-existing-static".into(),
        registry_id: "adopted-deployment".into(),
        expected_bindings: BTreeMap::from([("node".into(), "a".repeat(64))]),
        owners: BTreeMap::from([(
            "node".into(),
            domain::NodeIdentityAdoptionOwner {
                tenant_id: "local".into(),
                domain_id: "domain".into(),
                expires_at_ms: 0,
            },
        )]),
    }
}

/// A complete admin transition commits before publication; replay preserves receipt and new enrollment works.
#[tokio::test]
async fn static_adoption_is_authorized_durable_idempotent_and_adds_no_devices() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let command = adoption_command();
    let controller = identity_controller_with_policy(
        &log,
        integration::NodeIdentityPolicy::RequirePeerCertificate(command.expected_bindings.clone()),
    );
    let mut principal = OperatorPrincipal {
        subject: Some("actual-admin".into()),
        role: Some("operator".into()),
        ..Default::default()
    };
    let bytes = request(
        "POST",
        "/v1/node-identities/adopt",
        &serde_json::to_vec(&command).unwrap(),
        None,
    );
    assert!(
        route(&controller, &log, &principal, &bytes)
            .await
            .starts_with("HTTP/1.1 403")
    );
    assert!(log.load_checkpoint().unwrap().is_none());
    principal.role = Some(RECOVERY_ADMIN_ROLE.into());
    let first = route(&controller, &log, &principal, &bytes).await;
    assert!(first.starts_with("HTTP/1.1 200"), "{first}");
    let second = route(&controller, &log, &principal, &bytes).await;
    assert_eq!(
        first.split("\r\n\r\n").nth(1),
        second.split("\r\n\r\n").nth(1)
    );
    let checkpoint: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint.integration_json,
        log.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(10),
    )
    .unwrap();
    assert_eq!(
        restored
            .control()
            .node_identity_registry()
            .unwrap()
            .adoption
            .as_ref()
            .unwrap()
            .authority_subject,
        "actual-admin"
    );
    assert_eq!(controller.lock().unwrap().bridge.state().nodes().len(), 0);
    let mut conflict = command;
    conflict.owners.get_mut("node").unwrap().tenant_id = "foreign".into();
    let conflict = request(
        "POST",
        "/v1/node-identities/adopt",
        &serde_json::to_vec(&conflict).unwrap(),
        None,
    );
    assert!(
        route(&controller, &log, &principal, &conflict)
            .await
            .starts_with("HTTP/1.1 409")
    );
    let register = domain::IdentityCommand {
        command_id: "new-node-after-adoption".into(),
        node_id: "new-node".into(),
        expected_revision: 0,
        action: domain::IdentityAction::Register {
            tenant_id: "local".into(),
            domain_id: "domain".into(),
            digest: "b".repeat(64),
            expires_at_ms: 0,
        },
    };
    let result = route(
        &controller,
        &log,
        &principal,
        &request(
            "POST",
            "/v1/node-identities/commands",
            &serde_json::to_vec(&register).unwrap(),
            None,
        ),
    )
    .await;
    assert!(result.starts_with("HTTP/1.1 200"), "{result}");
    assert!(
        controller
            .lock()
            .unwrap()
            .bridge
            .control()
            .node_identity_registry()
            .unwrap()
            .records
            .contains_key("new-node")
    );
}

/// Stale original maps and failed SQL Commit change neither Control nor the shared static transport.
#[tokio::test]
async fn static_adoption_refuses_stale_map_and_sql_failure_without_partial_authority() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).unwrap();
    let command = adoption_command();
    let controller = identity_controller_with_policy(
        &log,
        integration::NodeIdentityPolicy::RequirePeerCertificate(command.expected_bindings.clone()),
    );
    let principal = OperatorPrincipal {
        subject: Some("admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        ..Default::default()
    };
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    let mut wrong = command.clone();
    wrong
        .expected_bindings
        .insert("node".into(), "b".repeat(64));
    let result = route(
        &controller,
        &log,
        &principal,
        &request(
            "POST",
            "/v1/node-identities/adopt",
            &serde_json::to_vec(&wrong).unwrap(),
            None,
        ),
    )
    .await;
    assert!(result.starts_with("HTTP/1.1 409"), "{result}");
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        before
    );
    assert!(log.load_checkpoint().unwrap().is_none());
    rusqlite::Connection::open(&path).unwrap().execute_batch("CREATE TRIGGER reject_adoption BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'adoption checkpoint outage'); END").unwrap();
    let result = route(
        &controller,
        &log,
        &principal,
        &request(
            "POST",
            "/v1/node-identities/adopt",
            &serde_json::to_vec(&command).unwrap(),
            None,
        ),
    )
    .await;
    assert!(result.starts_with("HTTP/1.1 409"), "{result}");
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        before
    );
    assert!(log.load_checkpoint().unwrap().is_none());
    assert!(
        controller
            .lock()
            .unwrap()
            .bridge
            .control()
            .node_identity_registry()
            .is_none()
    );
    let invalid = domain::NodeIdentityRegistry::new("cannot-publish-unproven-registry");
    assert!(
        integration::NodeIdentityPolicy::RequirePeerCertificate(command.expected_bindings)
            .validate_registry_projection(&invalid)
            .is_err()
    );
}
