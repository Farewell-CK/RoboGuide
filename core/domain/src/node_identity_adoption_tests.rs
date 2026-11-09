//! Original static-adoption receipts remain verifiable through ordinary lifecycle changes.

use crate::*;
use std::collections::BTreeMap;

/// Supplies explicit ownership for exactly one previously trusted leaf.
fn adoption() -> NodeIdentityAdoptionCommand {
    NodeIdentityAdoptionCommand {
        schema: NODE_IDENTITY_ADOPTION_SCHEMA.into(),
        command_id: "adopt-static".into(),
        registry_id: "deployment".into(),
        expected_bindings: BTreeMap::from([("node".into(), "a".repeat(64))]),
        owners: BTreeMap::from([(
            "node".into(),
            NodeIdentityAdoptionOwner {
                tenant_id: "local".into(),
                domain_id: "domain".into(),
                expires_at_ms: 100,
            },
        )]),
    }
}

/// Rotation/revocation preserve the original receipt; restore never refreshes clocks or ownership.
#[test]
fn static_adoption_survives_rotation_revocation_and_roundtrip() {
    let mut registry = adoption().build_registry(10, "admin").unwrap();
    registry.validate().unwrap();
    let original = registry.adoption.clone();
    registry
        .prepare_rotation("node", 1, "b".repeat(64))
        .unwrap();
    registry.activate_rotation("node", 2, 20, 5).unwrap();
    registry.revoke("node", 3).unwrap();
    registry.validate().unwrap();
    let restored: NodeIdentityRegistry =
        serde_json::from_slice(&serde_json::to_vec(&registry).unwrap()).unwrap();
    restored.validate().unwrap();
    assert_eq!(restored.adoption, original);
    assert!(!restored.accepts("node", &"a".repeat(64), 21));
    assert!(!restored.accepts("node", &"b".repeat(64), 21));
}

/// Incomplete ownership, reused leaves and an expired grant cannot initialize authority.
#[test]
fn static_adoption_requires_complete_unique_and_valid_explicit_ownership() {
    let original = adoption();
    let mut command = original.clone();
    command.owners.clear();
    assert!(command.build_registry(10, "admin").is_err());
    command = original.clone();
    command.owners.get_mut("node").unwrap().expires_at_ms = 10;
    assert!(command.build_registry(10, "admin").is_err());
    command = original.clone();
    command
        .expected_bindings
        .insert("other".into(), "a".repeat(64));
    command
        .owners
        .insert("other".into(), command.owners["node"].clone());
    assert!(command.build_registry(10, "admin").is_err());
    command = original;
    command.schema = "unknown".into();
    assert!(command.build_registry(10, "admin").is_err());
}

/// A new registry cannot masquerade as v0.1 or detach the retained original enrollment receipts.
#[test]
fn static_adoption_generation_and_original_receipts_are_required() {
    let original = adoption().build_registry(10, "admin").unwrap();
    let mut downgraded = original.clone();
    downgraded.schema = NODE_IDENTITY_REGISTRY_SCHEMA.into();
    assert!(downgraded.validate().is_err());
    let mut current = original.clone();
    current.adoption = None;
    assert!(current.validate().is_err());
    current = original.clone();
    current.commands.clear();
    assert!(current.validate().is_err());
    current = original;
    current.records.get_mut("node").unwrap().tenant_id = "foreign".into();
    assert!(current.validate().is_err());
}
