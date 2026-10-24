//! Cross-domain prepare/commit session authority tests.

use super::*;

fn session() -> FederationSession {
    FederationSession {
        transaction_id: "federation-1".into(),
        source_domain: "domain-a".into(),
        target_domain: "domain-b".into(),
        tenant_id: "tenant-a".into(),
        source_epoch: 7,
        plan_digest: format!("sha256:{}", "a".repeat(64)),
        ownership_digest: format!("sha256:{}", "b".repeat(64)),
        deadline: TimestampMs::new(100),
        state: FederationSessionState::Prepared,
    }
}

#[test]
fn federation_commit_is_epoch_and_digest_bound_and_checkpointed() {
    let mut control = ControlPlane::new();
    let value = session();
    control
        .prepare_federation_session(value.clone(), TimestampMs::new(0))
        .unwrap();
    control
        .prepare_federation_session(value.clone(), TimestampMs::new(0))
        .unwrap();
    assert!(!control.federation_dispatch_ready("federation-1", TimestampMs::new(10)));
    assert!(
        control
            .commit_federation_session(
                "federation-1",
                8,
                &value.plan_digest,
                &value.ownership_digest,
                TimestampMs::new(10),
            )
            .is_err()
    );
    control
        .commit_federation_session(
            "federation-1",
            7,
            &value.plan_digest,
            &value.ownership_digest,
            TimestampMs::new(10),
        )
        .unwrap();
    assert!(control.federation_dispatch_ready("federation-1", TimestampMs::new(50)));
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert!(restored.federation_dispatch_ready("federation-1", TimestampMs::new(50)));
    assert!(!restored.federation_dispatch_ready("federation-1", TimestampMs::new(100)));
}

/// A caller cannot skip Prepare by supplying Committed, or acquire authority for an absent legacy tenant.
#[test]
fn prepare_cannot_bootstrap_a_committed_or_unattributed_transaction() {
    let mut control = ControlPlane::new();
    let mut input = session();
    input.state = FederationSessionState::Committed;
    assert!(
        control
            .prepare_federation_session(input, TimestampMs::new(0))
            .is_err()
    );
    let mut legacy = session();
    legacy.tenant_id.clear();
    assert!(
        control
            .prepare_federation_session(legacy, TimestampMs::new(0))
            .is_err()
    );
    assert!(control.federation_session("federation-1").is_none());
}

/// Restoring old evidence preserves missing tenant history without granting a current service effect.
#[test]
fn legacy_tenant_absence_survives_restore_without_current_readiness() {
    let mut control = ControlPlane::new();
    let mut legacy = session();
    legacy.tenant_id.clear();
    legacy.state = FederationSessionState::Committed;
    control
        .federation_sessions
        .insert(legacy.transaction_id.clone(), legacy.clone());
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored.federation_session(&legacy.transaction_id),
        Some(&legacy)
    );
    assert!(!restored.federation_dispatch_ready(&legacy.transaction_id, TimestampMs::new(1)));
    assert!(
        restored
            .commit_federation_session(
                &legacy.transaction_id,
                legacy.source_epoch,
                &legacy.plan_digest,
                &legacy.ownership_digest,
                TimestampMs::new(1)
            )
            .is_err()
    );
}
