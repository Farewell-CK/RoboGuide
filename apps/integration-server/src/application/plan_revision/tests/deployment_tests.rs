//! Publication cannot replace an original runtime policy's deployment or retained source clocks.

use super::*;

/// Unmanaged legacy policies retain the exact original startup interpretation, including absence.
#[test]
fn legacy_runtime_policy_cannot_gain_a_startup_deployment_without_publication() {
    let fixture = Fixture::new();
    fixture.install();
    let profile = DeploymentFeasibility::from_document(
        serde_json::from_str(include_str!(
            "../../../../../../contracts/deployment-management/v0.1/fixtures/feasibility-v1.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let mut live = fixture.controller.lock().unwrap();
    assert!(attach_deployment(&mut live, Some(Arc::new(profile))).is_err());
    attach_deployment(&mut live, None).unwrap();
}

/// Publishes an explicitly synthetic original source through the real application transaction.
fn publish(fixture: &Fixture, revision: u64) {
    let document: serde_json::Value = serde_json::from_str(if revision == 1 {
        include_str!(
            "../../../../../../contracts/deployment-management/v0.1/fixtures/feasibility-v1.json"
        )
    } else {
        include_str!(
            "../../../../../../contracts/deployment-management/v0.1/fixtures/feasibility-v2.json"
        )
    })
    .unwrap();
    let command = serde_json::json!({"command_id":format!("publish-{revision}"),"registry_id":"unit-publications","expected_revision":revision-1,"revision":revision,"expected_physical_revision":0,"document":document});
    application::deployment_management::submit_feasibility(
        &fixture.controller,
        &fixture.log,
        fixture.writer.as_ref(),
        command.to_string().as_bytes(),
        "unit-publication-admin",
        &fixture.clock,
        None,
    )
    .unwrap();
}

/// Actual policy installation pins version one, while later requests and cold lookup see proper versions.
#[test]
fn online_deployment_keeps_original_runtime_policy_on_publication_and_cold_reattachment() {
    let fixture = Fixture::new();
    publish(&fixture, 1);
    fixture.install();
    let mission = fixture.plan.goal().mission_id().as_str();
    let original = fixture.controller.lock().unwrap().runtime_revisions[mission]
        .deployment_digest
        .clone();
    let state =
        serde_json::to_value(&fixture.controller.lock().unwrap().runtime_revisions).unwrap();
    publish(&fixture, 2);
    let mut live = fixture.controller.lock().unwrap();
    assert_ne!(
        original.as_deref(),
        application::deployment_management::current(&live, None)
            .unwrap()
            .as_ref()
            .map(|p| p.digest())
    );
    assert_eq!(
        state,
        serde_json::to_value(&live.runtime_revisions).unwrap()
    );
    let saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&live).unwrap()).unwrap();
    live.runtime_revisions = saved.runtime_revisions;
    live.published_deployments = saved.published_deployments;
    assert!(live.runtime_revisions[mission].deployment.is_none());
    attach_deployment(&mut live, None).unwrap();
    assert_eq!(
        original.as_deref(),
        live.runtime_revisions[mission]
            .deployment
            .as_ref()
            .map(|p| p.digest())
    );
    application::deployment_management::validate_restored(&live, None).unwrap();
    validate_restored(&live).unwrap();
}
