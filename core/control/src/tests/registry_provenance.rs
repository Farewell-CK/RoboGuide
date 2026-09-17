/// Returns a checkpoint with revision-one bindings and revision-five admitted topology evidence.
fn registry_provenance_checkpoint() -> ControlCheckpoint {
    let (plan, requirement) =
        physical_binding_plan("watermark", Some("entity-a"), Some("entity-b"), true);
    let group = ExecutionGroupId::new("watermark-group").unwrap();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let entries = &[
        ("entity-a", "node-a"),
        ("entity-b", "node-b"),
        ("unbound", "node-c"),
    ];
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(1, entries))
        .unwrap();
    create_ready_physical_group(&mut control, &plan, &requirement, &group, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group,
        &mut events,
    );
    bind_physical_task(&mut control, &group, &requirement, &committed, &mut events).unwrap();
    control
        .install_physical_entity_registry(physical_registry(5, entries))
        .unwrap();
    assert!(
        control
            .actor_bindings
            .values()
            .all(|binding| binding.registry_revision() == Some(1))
    );
    serde_json::from_str(&serde_json::to_string(&control.checkpoint()).unwrap()).unwrap()
}

/// Latest admission survives restart independently of historical bind provenance.
#[test]
fn registry_watermark_rejects_rollback_after_restart() {
    let mut restored = ControlPlane::restore(registry_provenance_checkpoint()).unwrap();
    assert!(restored.physical_entity_registry().is_none());
    assert!(
        restored
            .validate_physical_entity_registry_on_restore()
            .is_err()
    );
    let entries = &[
        ("entity-a", "node-a"),
        ("entity-b", "node-b"),
        ("unbound", "node-c"),
    ];
    assert!(
        matches!(restored.install_physical_entity_registry(physical_registry(2, entries)),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("revision moved backwards"))
    );
    assert!(restored.physical_entity_registry().is_none());
    restored
        .install_physical_entity_registry(physical_registry(5, entries))
        .unwrap();
    restored
        .validate_physical_entity_registry_on_restore()
        .unwrap();
    assert!(
        restored
            .actor_bindings
            .values()
            .all(|binding| binding.registry_revision() == Some(1))
    );
}

/// Equal-revision equivocation is rejected even if only an unbound entity changes routes.
#[test]
fn registry_watermark_rejects_same_revision_changed_content_after_restart() {
    let mut restored = ControlPlane::restore(registry_provenance_checkpoint()).unwrap();
    assert!(
        matches!(restored.install_physical_entity_registry(physical_registry(5,
        &[("entity-a", "node-a"), ("entity-b", "node-b"), ("unbound", "node-d")],
    )), Err(ControlError::InvalidProposal(reason)) if reason.contains("without a new revision"))
    );
    assert!(restored.physical_entity_registry().is_none());
    // Registration ordering is not content drift; the digest follows canonical entity order.
    restored
        .install_physical_entity_registry(physical_registry(
            5,
            &[
                ("unbound", "node-c"),
                ("entity-b", "node-b"),
                ("entity-a", "node-a"),
            ],
        ))
        .unwrap();
}

/// A rejected topology does not advance the watermark; accepted unbound updates do.
#[test]
fn registry_watermark_advances_only_after_all_binding_checks() {
    let mut restored = ControlPlane::restore(registry_provenance_checkpoint()).unwrap();
    let before = restored.registry_provenance.clone();
    assert!(matches!(
        restored.install_physical_entity_registry(physical_registry(
            9,
            &[
                ("entity-a", "node-b"),
                ("entity-b", "node-a"),
                ("unbound", "node-c")
            ],
        )),
        Err(ControlError::ActorBindingRequiresReconciliation { .. })
    ));
    assert_eq!(restored.registry_provenance, before);
    restored
        .install_physical_entity_registry(physical_registry(
            6,
            &[
                ("entity-a", "node-a"),
                ("entity-b", "node-b"),
                ("unbound", "node-d"),
            ],
        ))
        .unwrap();
    let mut again = ControlPlane::restore(restored.checkpoint()).unwrap();
    assert!(
        again
            .install_physical_entity_registry(physical_registry(
                5,
                &[
                    ("entity-a", "node-a"),
                    ("entity-b", "node-b"),
                    ("unbound", "node-c")
                ],
            ))
            .is_err()
    );
}

/// The watermark protects admitted revisions even before any Mission creates an ActorBinding.
#[test]
fn registry_watermark_without_bindings_is_durable_and_contains_no_routes() {
    let mut control = ControlPlane::new();
    control
        .install_physical_entity_registry(physical_registry(
            5,
            &[("private-entity", "private-node")],
        ))
        .unwrap();
    let encoded = serde_json::to_string(&control.checkpoint()).unwrap();
    assert!(!encoded.contains("private-entity"));
    assert!(!encoded.contains("private-node"));
    let mut restored = ControlPlane::restore(serde_json::from_str(&encoded).unwrap()).unwrap();
    assert!(restored.physical_entity_registry().is_none());
    assert!(
        restored
            .install_physical_entity_registry(physical_registry(
                2,
                &[("private-entity", "private-node")]
            ))
            .is_err()
    );
}

/// Old logical checkpoints remain readable; physical checkpoints need trusted missing provenance.
#[test]
fn registry_watermark_migration_fails_closed_for_unverifiable_physical_history() {
    let mut physical = serde_json::to_value(registry_provenance_checkpoint()).unwrap();
    physical
        .as_object_mut()
        .unwrap()
        .remove("registry_provenance");
    assert!(
        matches!(ControlPlane::restore(serde_json::from_value(physical).unwrap()),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("trusted checkpoint migration required"))
    );
    let mut legacy = serde_json::to_value(ControlPlane::new().checkpoint()).unwrap();
    legacy
        .as_object_mut()
        .unwrap()
        .remove("registry_provenance");
    let restored = ControlPlane::restore(serde_json::from_value(legacy).unwrap()).unwrap();
    assert!(restored.physical_entity_registry().is_none());
}

/// Malformed provenance cannot claim a revision lower than an already durable ActorBinding.
#[test]
fn registry_watermark_restore_rejects_binding_revision_above_watermark() {
    let mut checkpoint = serde_json::to_value(registry_provenance_checkpoint()).unwrap();
    checkpoint["registry_provenance"]["highest_revision"] = serde_json::json!(0);
    assert!(ControlPlane::restore(serde_json::from_value(checkpoint).unwrap()).is_err());
}
