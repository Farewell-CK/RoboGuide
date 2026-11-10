//! Original policy commands share real HTTP authorization and Controller checkpoint transactions.

use super::*;

/// Administrators retain exact policy receipts across duplicate, conflicting and cold requests.
#[tokio::test]
async fn retention_policy_is_admin_only_transactional_and_idempotent() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        state::CheckpointStoragePolicy::Full,
        state::ControllerStorageFormat::V3,
    )
    .unwrap();
    let controller = controller(&log);
    let admin = OperatorPrincipal {
        role: Some("recovery-admin".into()),
        subject: Some("test-admin".into()),
        ..Default::default()
    };
    let viewer = OperatorPrincipal {
        role: Some("viewer".into()),
        ..Default::default()
    };
    let endpoint = "/v1/maintenance/retention";
    for method in ["GET", "POST"] {
        let response = route(
            &controller,
            &log,
            &viewer,
            &request(method, endpoint, b"{}", None),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 403"), "{response}");
    }
    let response = route(
        &controller,
        &log,
        &admin,
        &request("GET", endpoint, b"", None),
    )
    .await;
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    assert_eq!(
        log.retention_policy().unwrap().policy.mode,
        state::RetentionMode::Evaluate
    );
    let policy = state::RetentionPolicy {
        mode: state::RetentionMode::Disabled,
        ..Default::default()
    };
    let mut command = state::RetentionPolicyCommand {
        command_id: "policy-1".into(),
        expected_revision: 0,
        policy,
    };
    let body = serde_json::to_vec(&command).unwrap();
    let first = route(
        &controller,
        &log,
        &admin,
        &request("POST", endpoint, &body, None),
    )
    .await;
    assert!(first.starts_with("HTTP/1.1 200"), "{first}");
    let original = log.control_command_journal().unwrap();
    let checkpoint = log.load_checkpoint().unwrap();
    let second = route(
        &controller,
        &log,
        &admin,
        &request("POST", endpoint, &body, None),
    )
    .await;
    assert_eq!(first, second);
    assert_eq!(log.control_command_journal().unwrap(), original);
    assert_eq!(log.load_checkpoint().unwrap(), checkpoint);
    command.policy.mode = state::RetentionMode::Enabled;
    let conflict = route(
        &controller,
        &log,
        &admin,
        &request(
            "POST",
            endpoint,
            &serde_json::to_vec(&command).unwrap(),
            None,
        ),
    )
    .await;
    assert!(conflict.starts_with("HTTP/1.1 409"), "{conflict}");
    assert_eq!(
        log.retention_policy().unwrap().policy.mode,
        state::RetentionMode::Disabled
    );
    assert!(log.write_status().unwrap().write_enabled);
    assert_eq!(log.control_command_journal().unwrap(), original);
    drop(controller);
    drop(log);
    let log = state::SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        state::CheckpointStoragePolicy::Full,
        state::ControllerStorageFormat::V3,
    )
    .unwrap();
    assert_eq!(log.retention_policy().unwrap().revision, 1);
    assert_eq!(log.control_command_journal().unwrap(), original);
    assert_eq!(log.load_checkpoint().unwrap(), checkpoint);
}

/// Existing deployments explicitly refuse retention rather than migrating as a read-side effect.
#[tokio::test]
async fn retention_reads_do_not_upgrade_legacy_deployments() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let admin = OperatorPrincipal {
        role: Some("recovery-admin".into()),
        ..Default::default()
    };
    let response = route(
        &controller,
        &log,
        &admin,
        &request("GET", "/v1/maintenance/retention", b"", None),
    )
    .await;
    assert!(response.starts_with("HTTP/1.1 409"), "{response}");
    assert!(log.events().unwrap().is_empty());
    assert_eq!(log.control_command_count().unwrap(), 0);
}
