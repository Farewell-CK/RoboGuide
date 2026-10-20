//! Negative authority checks; numeric leaf fixtures are not TLS, Node or device evidence.

use super::*;
use openraft::storage::RaftStateMachine;
use openraft::{CommittedLeaderId, Entry, EntryPayload, LogId, RaftSnapshotBuilder};

/// Creates canonical distinct digest fixtures solely for deterministic authorization rejection.
fn leaf(value: u64) -> String {
    format!("sha256:{value:064x}")
}

/// Creates empty original infrastructure authority without registering any embodied Nodes.
fn trust() -> CertificateTrust {
    CertificateTrust::initial((1..=3).map(|id| (id, leaf(id))).collect()).unwrap()
}

/// Stages one exact target for negative retirement and corrupted-restore cases.
fn stage(trust: &mut CertificateTrust) -> CertificateReceipt {
    trust
        .apply(CertificateMutation {
            request: CertificateRequest {
                command_id: "original-stage".into(),
                expected_revision: 0,
                member: 1,
                action: CertificateAction::Stage {
                    previous_leaf: leaf(1),
                    new_leaf: leaf(4),
                },
            },
            caller: 1,
            caller_leaf: leaf(1),
            probes: Vec::new(),
        })
        .unwrap()
}

/// Builds internal fixture evidence to test exact coverage; production never accepts caller proofs.
fn retirement(trust: &CertificateTrust) -> CertificateMutation {
    CertificateMutation {
        request: CertificateRequest {
            command_id: "original-retire".into(),
            expected_revision: 1,
            member: 1,
            action: CertificateAction::Retire {
                stage_command_id: "original-stage".into(),
                previous_leaf: leaf(1),
                new_leaf: leaf(4),
            },
        },
        caller: 2,
        caller_leaf: leaf(2),
        probes: (1..=3)
            .map(|member| CertificateProbe {
                member,
                presented_leaf: if member == 1 { leaf(4) } else { leaf(member) },
                revision: 1,
                trust_digest: trust.digest(),
                target: 1,
                target_leaf: leaf(4),
                observer_ca_bundle_digest: leaf(30),
                target_ca_bundle_digest: leaf(30),
            })
            .collect(),
    }
}

/// Missing, duplicated, stale or wrong-target confirmation cannot revoke a current member credential.
#[test]
fn retirement_requires_complete_exact_original_evidence() {
    let mut original = trust();
    stage(&mut original);
    let baseline = original.clone();
    let complete = retirement(&original);
    let mut missing = complete.clone();
    missing.probes.pop();
    let mut duplicate = complete.clone();
    duplicate.probes[2] = duplicate.probes[1].clone();
    let mut stale = complete.clone();
    stale.probes[1].revision = 0;
    let mut wrong_target = complete.clone();
    wrong_target.probes[1].target_leaf = leaf(1);
    let mut forged_caller = complete.clone();
    forged_caller.caller_leaf = leaf(8);
    for invalid in [missing, duplicate, stale, wrong_target, forged_caller] {
        assert!(original.apply(invalid).is_err());
        assert_eq!(original, baseline);
    }
    original.apply(complete).unwrap();
    assert!(!original.admits(1, &leaf(1)));
    assert!(original.admits(1, &leaf(4)));
    original.validate().unwrap();
}

/// Original result survives caller rotation, while reused IDs and retired credentials remain fenced.
#[test]
fn immutable_receipts_and_irreversible_retirement_survive_replay() {
    let mut original = trust();
    let receipt = stage(&mut original);
    original.apply(retirement(&original)).unwrap();
    let mut replay = CertificateMutation {
        request: receipt.request.clone(),
        caller: receipt.caller,
        caller_leaf: leaf(4),
        probes: Vec::new(),
    };
    assert_eq!(original.apply(replay.clone()).unwrap(), receipt);
    let mut retired_caller = replay.clone();
    retired_caller.caller_leaf = leaf(1);
    assert!(original.apply(retired_caller).is_err());
    replay.request.member = 3;
    assert!(original.apply(replay).is_err());
    let projection = original.clone();
    let reuse = CertificateMutation {
        request: CertificateRequest {
            command_id: "reuse-retired".into(),
            expected_revision: 2,
            member: 3,
            action: CertificateAction::Stage {
                previous_leaf: leaf(3),
                new_leaf: leaf(1),
            },
        },
        caller: 2,
        caller_leaf: leaf(2),
        probes: Vec::new(),
    };
    assert!(original.apply(reuse).is_err());
    assert_eq!(original, projection);
    let restored: CertificateTrust =
        serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
    restored.validate().unwrap();
    assert_eq!(restored, original);
}

/// Altered materialized state, original proofs and missing history cannot authorize restored TLS.
#[test]
fn restore_replays_original_trust_and_rejects_projection_or_history_forks() {
    let mut original = trust();
    stage(&mut original);
    original.apply(retirement(&original)).unwrap();
    let mut resurrected = original.clone();
    resurrected.members.get_mut(&1).unwrap().current = leaf(1);
    assert!(resurrected.validate().is_err());
    let mut missing = original.clone();
    missing.receipts.remove(0);
    assert!(missing.validate().is_err());
    let mut altered_proof = original.clone();
    altered_proof.receipts[1].probes[2].trust_digest = leaf(99);
    assert!(altered_proof.validate().is_err());
    let mut altered_revision = original;
    altered_revision.receipts[0].revision += 1;
    assert!(altered_revision.validate().is_err());
}

/// Lost-key abort revokes only the pending credential and cannot make it reusable later.
#[test]
fn explicit_abort_retains_original_owner_and_irreversibly_retires_unused_credential() {
    let mut original = trust();
    stage(&mut original);
    let request = CertificateRequest {
        command_id: "original-abort".into(),
        expected_revision: 1,
        member: 1,
        action: CertificateAction::Abort {
            stage_command_id: "original-stage".into(),
            previous_leaf: leaf(1),
            new_leaf: leaf(4),
        },
    };
    let mutation = CertificateMutation {
        request,
        caller: 2,
        caller_leaf: leaf(2),
        probes: Vec::new(),
    };
    let receipt = original.apply(mutation.clone()).unwrap();
    assert!(original.admits(1, &leaf(1)));
    assert!(!original.admits(1, &leaf(4)));
    assert!(original.members[&1].staged.is_none());
    assert_eq!(original.apply(mutation).unwrap(), receipt);
    let before = original.clone();
    let reused = CertificateMutation {
        request: CertificateRequest {
            command_id: "reuse-aborted-leaf".into(),
            expected_revision: 2,
            member: 1,
            action: CertificateAction::Stage {
                previous_leaf: leaf(1),
                new_leaf: leaf(4),
            },
        },
        caller: 2,
        caller_leaf: leaf(2),
        probes: Vec::new(),
    };
    assert!(original.apply(reused).is_err());
    assert_eq!(original, before);
    original.validate().unwrap();
}

/// Trust snapshots retain original application bytes and cannot roll retired identities backwards.
#[tokio::test]
async fn original_sql_snapshot_preserves_application_and_fences_old_trust() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("original.sqlite3");
    let mut store = SqliteRaftStore::open(&path, "original-v03", "test/checkpoint-v1").unwrap();
    store
        .initialize_certificate_trust(&trust().bootstrap)
        .unwrap();
    let application = crate::tests::batch(None, 1);
    store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
            payload: EntryPayload::Normal(application.into()),
        }])
        .await
        .unwrap();
    let original_application = serde_json::to_vec(&store.accepted_view().unwrap()).unwrap();
    let old_snapshot = store.build_snapshot().await.unwrap();
    let mut fixture = trust();
    let staged = stage(&mut fixture);
    let first = CertificateMutation {
        request: staged.request,
        caller: staged.caller,
        caller_leaf: staged.caller_leaf,
        probes: staged.probes,
    };
    for (index, mutation) in [first, retirement(&fixture)].into_iter().enumerate() {
        let result = store
            .apply([Entry {
                log_id: LogId::new(CommittedLeaderId::new(1, 1), index as u64 + 2),
                payload: EntryPayload::Normal(ReplicaCommand::Certificate {
                    certificate: mutation,
                }),
            }])
            .await
            .unwrap();
        assert!(result[0].accepted);
        assert!(result[0].certificate.is_some());
        assert_eq!(
            serde_json::to_vec(&store.accepted_view().unwrap()).unwrap(),
            original_application
        );
    }
    let committed_trust = store.certificate_trust().unwrap().unwrap();
    assert!(!committed_trust.admits(1, &leaf(1)));
    assert!(
        store
            .install_snapshot(&old_snapshot.meta, old_snapshot.snapshot)
            .await
            .is_err()
    );
    assert_eq!(store.certificate_trust().unwrap().unwrap(), committed_trust);
    assert_eq!(
        serde_json::to_vec(&store.accepted_view().unwrap()).unwrap(),
        original_application
    );
    let snapshot = store.build_snapshot().await.unwrap();
    let mut target = SqliteRaftStore::open(
        &directory.path().join("new.sqlite3"),
        "original-v03",
        "test/checkpoint-v1",
    )
    .unwrap();
    target
        .initialize_certificate_trust(&trust().bootstrap)
        .unwrap();
    target
        .install_snapshot(&snapshot.meta, snapshot.snapshot)
        .await
        .unwrap();
    assert_eq!(
        target.certificate_trust().unwrap().unwrap(),
        committed_trust
    );
    assert_eq!(
        serde_json::to_vec(&target.accepted_view().unwrap()).unwrap(),
        original_application
    );
    drop(store);
    let reopened = SqliteRaftStore::open(&path, "original-v03", "test/checkpoint-v1").unwrap();
    assert_eq!(
        reopened.certificate_trust().unwrap().unwrap(),
        committed_trust
    );
    assert!(
        reopened
            .initialize_certificate_trust(&trust().bootstrap)
            .is_ok()
    );
    let mut fork = trust().bootstrap;
    fork.insert(1, leaf(9));
    assert!(reopened.initialize_certificate_trust(&fork).is_err());
}
