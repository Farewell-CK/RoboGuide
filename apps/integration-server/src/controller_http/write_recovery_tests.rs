//! Production-handler recovery scope and replay checks; actual process injection is independent.

use super::*;

/// Recovery is global administration and cannot inherit viewer/operator or tenant-only privileges.
#[tokio::test]
async fn disabled_authority_preserves_queries_and_requires_global_recovery_admin() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .unwrap()
        .with_write_failure_fence("original-http-process")
        .unwrap();
    let controller = controller(&log);
    let original = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    log.begin_batch().unwrap();
    save_controller_checkpoint(&log, &original).unwrap();
    log.commit_batch().unwrap();
    log.disable_writes("original SQL outage", false).unwrap();
    let status = log.write_status().unwrap();
    let command = state::WriteRecoveryCommand {
        schema_version: "roboguide.authority-write-recovery/v0.1".into(),
        command_id: "original-http-recovery".into(),
        process_id: status.process_id.clone().unwrap(),
        generation: status.generation,
        checkpoint_identity: status.checkpoint_identity.clone().unwrap(),
        authority_revision: status.authority_revision,
    };
    let body = serde_json::to_vec(&command).unwrap();
    for role in ["viewer", "operator"] {
        let principal = OperatorPrincipal {
            subject: Some(format!("actual-{role}")),
            role: Some(role.into()),
            ..Default::default()
        };
        let denied = route(
            &controller,
            &log,
            &principal,
            &request("POST", "/v1/authority/recover", &body, None),
        )
        .await;
        assert!(denied.starts_with("HTTP/1.1 403"), "{denied}");
        let status_view = route(
            &controller,
            &log,
            &principal,
            &request("GET", "/v1/authority-status", b"", None),
        )
        .await;
        assert!(status_view.starts_with("HTTP/1.1 200"));
        assert!(status_view.contains("\"write_enabled\":false"));
        let ready = route(
            &controller,
            &log,
            &principal,
            &request("GET", "/readyz", b"", None),
        )
        .await;
        assert!(ready.starts_with("HTTP/1.1 503"));
    }
    let scoped = OperatorPrincipal {
        tenants: Some(BTreeSet::from(["tenant-only".into()])),
        subject: Some("tenant-admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        ..Default::default()
    };
    assert!(
        route(
            &controller,
            &log,
            &scoped,
            &request("POST", "/v1/authority/recover", &body, None)
        )
        .await
        .starts_with("HTTP/1.1 403")
    );
    let administrator = OperatorPrincipal {
        subject: Some("global-admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        ..Default::default()
    };
    let blocked = route(
        &controller,
        &log,
        &administrator,
        &request("POST", "/v1/missions", b"{}", None),
    )
    .await;
    assert!(blocked.starts_with("HTTP/1.1 503"), "{blocked}");
    assert_eq!(log.write_status().unwrap(), status);
    let before_events = log.events().unwrap();
    let recovered = route(
        &controller,
        &log,
        &administrator,
        &request("POST", "/v1/authority/recover", &body, None),
    )
    .await;
    assert!(recovered.starts_with("HTTP/1.1 200"), "{recovered}");
    assert!(recovered.contains("\"authority_subject\":\"global-admin\""));
    assert_eq!(
        route(
            &controller,
            &log,
            &administrator,
            &request("POST", "/v1/authority/recover", &body, None)
        )
        .await,
        recovered
    );
    assert!(log.write_status().unwrap().write_enabled);
    assert_eq!(log.events().unwrap(), before_events);
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        original
    );
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        original
    );
    assert_eq!(log.control_command_count().unwrap(), 2);
}
