//! Workflow identity reconciliation tests.

use super::*;

/// Changed local workflow configuration fences active persisted execution state.
#[test]
fn active_execution_does_not_reconcile_through_changed_workflow() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let catalog = gated_catalog(
        "http://127.0.0.1:50051".to_string(),
        state_dir.path().to_path_buf(),
    );
    let journal_path = crate::journal_path(state_dir.path());
    let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
    let invocation = serde_json::json!({
        "capability_contract": "mobility.reach_region@v1",
        "parameters": {},
        "resource_ids": ["base"],
    });
    let spec = crate::ExecutionSpec::new(
        serde_json::to_vec(&invocation).expect("invocation serializes"),
        "obsolete-workflow-digest",
        vec!["base".to_string()],
    )
    .expect("execution spec is valid");
    journal
        .prepare_dispatch("running-old-config", &spec)
        .expect("dispatch is prepared");
    journal
        .record_local_handle("running-old-config", "local-1")
        .expect("handle records");
    journal
        .record_status(
            "running-old-config",
            1,
            crate::JournalStatus::Running,
            "moving",
        )
        .expect("running status records");
    drop(journal);

    let engine = crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(false)))) as Arc<dyn LocalDriver>],
    )
    .expect("engine opens journal");
    assert!(matches!(
        engine.recover(),
        Err(crate::EngineError::ReconciliationRequired(_))
    ));
    let audit = crate::ExecutionJournal::open(&journal_path).expect("audit journal opens");
    assert_eq!(
        audit
            .get("running-old-config")
            .expect("record reads")
            .expect("record exists")
            .status(),
        crate::JournalStatus::ReconciliationRequired
    );
}
