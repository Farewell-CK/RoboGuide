//! Original production-handler deployment updates, rollback and explicit cold reattachment.

use super::*;

/// Supplies an actual authenticated global administrator or a scoped/ordinary principal.
fn principal(role: &str, tenant: &str) -> OperatorPrincipal {
    OperatorPrincipal {
        subject: Some("deployment-admin".into()),
        role: Some(role.into()),
        tenants: Some(BTreeSet::from([tenant.into()])),
        ..Default::default()
    }
}

/// A real writer turn whose transport authority becomes unavailable after SQL preparation.
struct WithdrawnGate(application::ApplicationWriter);

impl application::ApplicationWriteGate for WithdrawnGate {
    /// Holds the actual original writer while admitting this private unit transaction.
    fn lock(&self) -> Result<application::WriteGuard<'_>, String> {
        self.0.lock()
    }

    /// Models the production gate's current-certificate/role recheck before final Commit.
    fn validate_locked(&self) -> Result<(), String> {
        Err("transport authority withdrawn during SQL preparation".into())
    }
}

/// Loss of authorization after staging SQL must roll back both authority and its command receipt.
#[test]
fn online_publication_rechecks_current_authority_before_commit() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    let gate = WithdrawnGate(Default::default());
    let result = application::deployment_management::submit_physical(
        &controller,
        &log,
        &gate,
        physical(1).to_string().as_bytes(),
        "unit-admin",
        &runtime::FixedClock::new(domain::TimestampMs::new(10)),
    );
    assert!(result.unwrap_err().contains("withdrawn"));
    assert_eq!(
        before,
        server_checkpoint_json(&controller.lock().unwrap()).unwrap()
    );
    assert!(log.load_checkpoint().unwrap().is_none());
    assert_eq!(log.control_command_count().unwrap(), 0);
}

/// Returns a complete physical source in the existing external deployment file format.
fn physical(revision: u64) -> serde_json::Value {
    serde_json::json!({"command_id":format!("physical-{revision}"),"expected_revision":revision-1,"registry":{"schema":PHYSICAL_ENTITY_REGISTRY_SCHEMA,"registry_id":"fixture-entities","revision":revision,"routing_profile":"one-routable-entity-per-node","entities":[{"entity_id":"entity-a","node_id":"existing"}]}})
}

/// Publishes one independently validated synthetic feasibility source without any Node facts.
pub(super) fn feasibility(version: u64) -> serde_json::Value {
    let source = if version == 1 {
        include_str!(
            "../../../../contracts/deployment-management/v0.1/fixtures/feasibility-v1.json"
        )
    } else {
        include_str!(
            "../../../../contracts/deployment-management/v0.1/fixtures/feasibility-v2.json"
        )
    };
    serde_json::json!({"command_id":format!("feasibility-{version}"),"registry_id":"fixture-publications","expected_revision":version-1,"revision":version,"expected_physical_revision":0,"document":serde_json::from_str::<serde_json::Value>(source).unwrap()})
}

/// Physical declarations commit atomically, keep original replay receipts and require cold source evidence.
#[tokio::test]
async fn online_physical_registry_permissions_revision_replay_and_restore() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let command = request(
        "POST",
        "/v1/physical-entities",
        &serde_json::to_vec(&physical(1)).unwrap(),
        None,
    );
    for who in [
        principal("operator", "*"),
        principal("recovery-admin", "foreign"),
    ] {
        assert!(
            route(&controller, &log, &who, &command)
                .await
                .starts_with("HTTP/1.1 403")
        );
    }
    let admin = principal("recovery-admin", "*");
    let response = route(&controller, &log, &admin, &command).await;
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    assert_eq!(response, route(&controller, &log, &admin, &command).await);
    let view = route(
        &controller,
        &log,
        &admin,
        &request("GET", "/v1/physical-entities", b"", None),
    )
    .await;
    assert!(view.contains("entity-a"));
    let checkpoint = controller.lock().unwrap().bridge.control().checkpoint();
    let restored = control::ControlPlane::restore(checkpoint).unwrap();
    assert!(restored.physical_entity_registry().is_none());
    let mut cold = controller.lock().unwrap().clone();
    cold.bridge = IntegrationRuntimeBridge::new(
        restored,
        state::InMemorySharedNodeState::new(),
        log.clone(),
        integration::GrpcNodeRouter::default(),
    );
    assert!(application::deployment_management::validate_physical_restore(&cold).is_err());
    let snapshot = application::load_physical_entity_registry_file(&{
        let path = directory.path().join("current.json");
        std::fs::write(&path, physical(1)["registry"].to_string()).unwrap();
        path
    })
    .unwrap();
    cold.bridge
        .control_mut()
        .install_physical_entity_registry(snapshot)
        .unwrap();
    application::deployment_management::validate_physical_restore(&cold).unwrap();
    let mut stale = physical(1);
    stale["command_id"] = "stale".into();
    assert!(
        route(
            &controller,
            &log,
            &admin,
            &request(
                "POST",
                "/v1/physical-entities",
                stale.to_string().as_bytes(),
                None
            )
        )
        .await
        .starts_with("HTTP/1.1 409")
    );
    assert!(controller.lock().unwrap().bridge.state().nodes().is_empty());
}

/// SQL failure cannot publish a physical table, provenance or original management receipt.
#[tokio::test]
async fn online_deployment_sql_rollback_preserves_all_live_authority() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).unwrap();
    let controller = controller(&log);
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    rusqlite::Connection::open(&path).unwrap().execute_batch("CREATE TRIGGER reject_deployment BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'deployment checkpoint outage'); END").unwrap();
    for (url, input) in [
        ("/v1/physical-entities", physical(1)),
        ("/v1/deployment-feasibility", feasibility(1)),
    ] {
        let reply = route(
            &controller,
            &log,
            &principal("recovery-admin", "*"),
            &request("POST", url, input.to_string().as_bytes(), None),
        )
        .await;
        assert!(reply.starts_with("HTTP/1.1 409"), "{reply}");
        assert_eq!(
            before,
            server_checkpoint_json(&controller.lock().unwrap()).unwrap()
        );
    }
}

/// Published versions retain exact original source documents, receipts and original-policy lookup.
#[tokio::test]
async fn online_feasibility_versions_keep_original_interpretations_and_validate_restore() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let admin = principal("recovery-admin", "*");
    let input = feasibility(1);
    let bytes = request(
        "POST",
        "/v1/deployment-feasibility",
        input.to_string().as_bytes(),
        None,
    );
    let first = route(&controller, &log, &admin, &bytes).await;
    assert!(first.starts_with("HTTP/1.1 200"), "{first}");
    let old = application::deployment_management::current(&controller.lock().unwrap(), None)
        .unwrap()
        .unwrap();
    let input2 = feasibility(2);
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    for change in [
        "source-digest",
        "physical-version",
        "publication-version",
        "registry-id",
    ] {
        let mut invalid = input2.clone();
        invalid["command_id"] = format!("invalid-{change}").into();
        match change {
            "source-digest" => invalid["document"]["digest"] = "sha256:invalid".into(),
            "physical-version" => invalid["expected_physical_revision"] = 10.into(),
            "publication-version" => invalid["expected_revision"] = 0.into(),
            _ => invalid["registry_id"] = "other-publication-authority".into(),
        }
        let response = route(
            &controller,
            &log,
            &admin,
            &request(
                "POST",
                "/v1/deployment-feasibility",
                invalid.to_string().as_bytes(),
                None,
            ),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 409"), "{change}: {response}");
        assert_eq!(
            before,
            server_checkpoint_json(&controller.lock().unwrap()).unwrap()
        );
    }
    let response = route(
        &controller,
        &log,
        &admin,
        &request(
            "POST",
            "/v1/deployment-feasibility",
            input2.to_string().as_bytes(),
            None,
        ),
    )
    .await;
    assert!(response.starts_with("HTTP/1.1 200"), "{response}");
    assert_eq!(first, route(&controller, &log, &admin, &bytes).await);
    let mut cold = controller.lock().unwrap().clone();
    assert_ne!(
        old.digest(),
        application::deployment_management::current(&cold, None)
            .unwrap()
            .unwrap()
            .digest()
    );
    assert_eq!(
        old.digest(),
        application::deployment_management::original(&cold, Some(old.digest()), None)
            .unwrap()
            .unwrap()
            .digest()
    );
    application::deployment_management::validate_restored(&cold, None).unwrap();
    let saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&cold).unwrap()).unwrap();
    application::deployment_management::validate_wrapper(&saved).unwrap();
    let mut hidden = saved.clone();
    hidden.schema = PRE_OBSERVABILITY_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(application::deployment_management::validate_wrapper(&hidden).is_err());
    cold.published_deployments = saved.published_deployments;
    application::deployment_management::validate_restored(&cold, None).unwrap();
    let mut corrupt = serde_json::to_value(cold.published_deployments.as_ref().unwrap()).unwrap();
    corrupt["history"]["1"]["identity"]["run_id"] = "changed-original-evidence".into();
    cold.published_deployments = Some(serde_json::from_value(corrupt).unwrap());
    assert!(application::deployment_management::validate_restored(&cold, None).is_err());
    cold.published_deployments = None;
    assert!(application::deployment_management::validate_restored(&cold, None).is_err());
    assert!(controller.lock().unwrap().bridge.state().nodes().is_empty());
}
