//! Synthetic upgrade-contract counterexamples; no support, Nodes or devices are claimed as deployed.

use super::*;
use openraft::{CommittedLeaderId, LogId, Membership};

/// Constructs an explicitly synthetic complete three-member original source intent.
fn original_intent() -> ApplicationUpgradeIntent {
    let membership = StoredMembership::new(
        Some(LogId::new(CommittedLeaderId::new(2, 1), 1)),
        Membership::new(
            vec![BTreeSet::from([1, 2, 3])],
            (1..=3)
                .map(|member| {
                    (
                        member,
                        ReplicaMember::with_content_store(
                            format!("127.0.0.1:{}", 28000 + member),
                            format!("replica-content:{member:064x}"),
                        )
                        .unwrap(),
                    )
                })
                .collect::<BTreeMap<_, _>>(),
        ),
    );
    let target = UpgradeBinary {
        application_schema: "roboguide.controller-checkpoint/v56".into(),
        binary_digest: format!("sha256:{}", "b".repeat(64)),
        policy_digest: "c".repeat(64),
    };
    let predecessor = AcceptedApplicationIdentity {
        revision: 0,
        event_sequence: 0,
        checkpoint_digest: None,
    };
    ApplicationUpgradeIntent {
        request: ApplicationUpgradeRequest {
            schema: "roboguide.application-upgrade/v0.1".into(),
            command_id: "synthetic-original-upgrade".into(),
            expected_revision: 0,
            from_schema: "roboguide.controller-checkpoint/v53".into(),
            target: target.clone(),
            budget_ms: 10000,
        },
        epoch: ControllerEpoch {
            term: 2,
            replica_id: 1,
        },
        membership: membership.clone(),
        predecessor: predecessor.clone(),
        supports: (1..=3)
            .map(|member| ApplicationUpgradeSupport {
                member,
                leaf: format!("sha256:{member:064x}"),
                infrastructure_binary_digest: format!("sha256:{}", "a".repeat(64)),
                target: target.clone(),
                membership: membership.clone(),
                predecessor: predecessor.clone(),
            })
            .collect(),
    }
}

/// Creates only a synthetic complete receipt; actual process checks must independently establish waits.
fn activation() -> ApplicationUpgradeReceipt {
    let intent = original_intent();
    let drains = intent
        .supports
        .iter()
        .map(|proof| ApplicationUpgradeDrain {
            member: proof.member,
            leaf: proof.leaf.clone(),
            intent_digest: intent_digest(&intent),
            proof: ControllerDrainProof::NoApplicationProcess {
                supervisor_instance: format!(
                    "00000000-0000-4000-8000-{member:012x}",
                    member = proof.member
                ),
                epoch: intent.epoch,
            },
        })
        .collect();
    ApplicationUpgradeReceipt {
        intent,
        drains,
        revision: 1,
    }
}

/// Finite old reading support never includes unknown/future generations or schema aliases.
#[test]
fn original_reader_window_is_explicit_and_bounded() {
    for generation in 53..=60 {
        assert!(supports_application_generation(&format!(
            "roboguide.controller-checkpoint/v{generation}"
        )));
    }
    for schema in [
        "",
        "roboguide.controller-checkpoint/v52",
        "roboguide.controller-checkpoint/v61",
        "roboguide.controller-checkpoint/v056",
        "other/v56",
    ] {
        assert!(!supports_application_generation(schema));
    }
}

/// Actual Raft JSON round trips complete numeric membership maps without changing the original envelope.
#[test]
fn upgrade_log_envelope_round_trips_original_numeric_member_bindings() {
    let command = ReplicaCommand::ApplicationUpgrade {
        application_upgrade: Box::new(activation()),
    };
    let raw = serde_json::to_vec(&command).unwrap();
    let restored: ReplicaCommand = serde_json::from_slice(&raw).unwrap();
    assert_eq!(serde_json::to_vec(&restored).unwrap(), raw);
    let mut hidden: serde_json::Value = serde_json::from_slice(&raw).unwrap();
    hidden["unexpected"] = serde_json::json!(true);
    assert!(serde_json::from_value::<ReplicaCommand>(hidden).is_err());
}

/// Cached executable hashes cannot survive a changed file or another same-byte inode at the configured name.
#[test]
fn actual_executable_witness_refuses_changes_and_replacement() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("original-executable");
    std::fs::write(&path, b"original bytes").unwrap();
    let witness = BinaryWitness::capture(&path).unwrap();
    witness.require_current(&path).unwrap();
    std::fs::write(&path, b"changed and longer bytes").unwrap();
    assert!(witness.require_current(&path).is_err());
    std::fs::write(&path, b"original bytes").unwrap();
    let witness = BinaryWitness::capture(&path).unwrap();
    let replacement = directory.path().join("replacement");
    std::fs::write(&replacement, b"original bytes").unwrap();
    std::fs::rename(replacement, &path).unwrap();
    assert!(witness.require_current(&path).is_err());
}

/// Missing voters, altered unit-free metadata and source identities cannot become accepted support.
#[test]
fn upgrade_requires_the_complete_original_member_support_and_real_target_identity() {
    let original = original_intent();
    original.validate().unwrap();
    let mut missing = original.clone();
    missing.supports.pop();
    assert!(missing.validate().is_err());
    let mut duplicate = original.clone();
    duplicate.supports[1] = duplicate.supports[0].clone();
    assert!(duplicate.validate().is_err());
    let mut target = original.clone();
    target.supports[0].target.binary_digest = format!("sha256:{}", "d".repeat(64));
    assert!(target.validate().is_err());
    let mut predecessor = original.clone();
    predecessor.supports[0].predecessor.event_sequence = 1;
    assert!(predecessor.validate().is_err());
    let mut downgrade = original.clone();
    downgrade.request.from_schema = "roboguide.controller-checkpoint/v55".into();
    downgrade.request.target.application_schema = "roboguide.controller-checkpoint/v54".into();
    assert!(downgrade.validate().is_err());
}

/// Original all-member waits and canonical source hashes remain separate from current writer version labels.
#[test]
fn activation_retains_original_drains_and_rejects_changed_history() {
    let original = activation();
    let authority = ApplicationUpgradeAuthority {
        schema: "roboguide.application-upgrade-authority/v0.1".into(),
        original_schema: original.intent.request.from_schema.clone(),
        receipts: vec![original],
        aborts: Vec::new(),
    };
    authority
        .validate("roboguide.controller-checkpoint/v53")
        .unwrap();
    assert_eq!(
        authority.active_schema(),
        "roboguide.controller-checkpoint/v56"
    );
    let mut missing = authority.clone();
    missing.receipts[0].drains.pop();
    assert!(missing.validate(&missing.original_schema).is_err());
    let mut foreign = authority.clone();
    foreign.receipts[0].drains[0].intent_digest = format!("sha256:{}", "e".repeat(64));
    assert!(foreign.validate(&foreign.original_schema).is_err());
    let mut epoch = authority.clone();
    if let ControllerDrainProof::NoApplicationProcess { epoch, .. } =
        &mut epoch.receipts[0].drains[0].proof
    {
        epoch.term += 1;
    }
    assert!(epoch.validate(&epoch.original_schema).is_err());
}

/// An activated target may read only its exact frozen old checkpoint before normal migration.
#[test]
fn upgrade_does_not_relabel_or_mutate_the_original_checkpoint() {
    let mut receipt = activation();
    let checkpoint = state::PersistedCheckpoint {
        schema: receipt.intent.request.from_schema.clone(),
        event_sequence: 7,
        checkpoint_json: "original immutable checkpoint bytes".into(),
    };
    let predecessor = AcceptedApplicationIdentity {
        revision: 5,
        event_sequence: 7,
        checkpoint_digest: Some(state::checkpoint_identity(&checkpoint)),
    };
    receipt.intent.predecessor = predecessor.clone();
    for support in &mut receipt.intent.supports {
        support.predecessor = predecessor.clone();
    }
    let hash = intent_digest(&receipt.intent);
    for proof in &mut receipt.drains {
        proof.intent_digest = hash.clone();
    }
    let authority = ApplicationUpgradeAuthority {
        schema: "roboguide.application-upgrade-authority/v0.1".into(),
        original_schema: checkpoint.schema.clone(),
        receipts: vec![receipt],
        aborts: Vec::new(),
    };
    authority.validate(&checkpoint.schema).unwrap();
    assert!(authority.permits_checkpoint(&checkpoint));
    let mut changed = checkpoint.clone();
    changed.checkpoint_json.push('!');
    assert!(!authority.permits_checkpoint(&changed));
    changed.schema = "roboguide.controller-checkpoint/v56".into();
    assert!(authority.permits_checkpoint(&changed));
    assert!(!authority.permits_checkpoint(&state::PersistedCheckpoint {
        schema: "roboguide.controller-checkpoint/v55".into(),
        ..checkpoint
    }));
}

/// Abandonment cannot overlap an activation or release an unconfirmed local wait.
#[test]
fn abandonment_retains_original_wait_fence_and_forbids_downgrade() {
    let intent = original_intent();
    let abort = ApplicationUpgradeAbort {
        command_id: "synthetic-original-abandonment".into(),
        epoch: ControllerEpoch {
            term: 3,
            replica_id: 2,
        },
        membership: intent.membership.clone(),
        intent: intent.clone(),
    };
    let mut authority = ApplicationUpgradeAuthority {
        schema: "roboguide.application-upgrade-authority/v0.1".into(),
        original_schema: intent.request.from_schema.clone(),
        receipts: Vec::new(),
        aborts: vec![abort],
    };
    authority.validate(&authority.original_schema).unwrap();
    assert_eq!(authority.active_schema(), intent.request.from_schema);
    let mut operation = LocalUpgradeOperation {
        intent,
        clock: ContentBudget::issue(10000).unwrap(),
        process: None,
        process_history_head: 0,
        supervisor_instance: "00000000-0000-4000-8000-000000000001".into(),
        drain: None,
    };
    assert!(!resolved(&operation, Some(&authority)));
    operation.drain = Some(activation().drains[0].clone());
    assert!(resolved(&operation, Some(&authority)));
    authority.receipts.push(activation());
    assert!(authority.validate(&authority.original_schema).is_err());
}

/// A distinct same-schema repair never authorizes an old activated executable again.
#[test]
fn same_schema_patch_preserves_monotonic_binary_history() {
    let original = activation();
    let mut patch = original.clone();
    patch.revision = 2;
    patch.intent.request.command_id = "synthetic-original-patch".into();
    patch.intent.request.expected_revision = 1;
    patch.intent.request.from_schema = original.intent.request.target.application_schema.clone();
    patch.intent.request.target.binary_digest = format!("sha256:{}", "d".repeat(64));
    patch.intent.request.target.policy_digest = "e".repeat(64);
    for support in &mut patch.intent.supports {
        support.target = patch.intent.request.target.clone();
    }
    for drain in &mut patch.drains {
        drain.intent_digest = intent_digest(&patch.intent);
    }
    let mut authority = ApplicationUpgradeAuthority {
        schema: "roboguide.application-upgrade-authority/v0.1".into(),
        original_schema: original.intent.request.from_schema.clone(),
        receipts: vec![original, patch],
        aborts: Vec::new(),
    };
    authority.validate(&authority.original_schema).unwrap();
    let retired = authority.receipts[0].intent.request.target.clone();
    let patch = &mut authority.receipts[1];
    patch.intent.request.target = retired;
    for support in &mut patch.intent.supports {
        support.target = patch.intent.request.target.clone();
    }
    for drain in &mut patch.drains {
        drain.intent_digest = intent_digest(&patch.intent);
    }
    assert!(authority.validate(&authority.original_schema).is_err());
}
