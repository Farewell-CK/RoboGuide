//! Certificate lifecycle tests use only digests and receive-time policy; no TLS device exists.

use super::node_identity::*;

/// Rotation admits only the new certificate after a bounded overlap and then revocation rejects both.
#[test]
fn rotation_overlap_and_revocation_are_revision_bound() {
    let old = "a".repeat(64);
    let next = "b".repeat(64);
    let mut registry = NodeIdentityRegistry::new("test-registry");
    registry
        .register("node-a", "tenant-a", "domain-a", old.clone(), 10)
        .unwrap();
    assert!(registry.accepts("node-a", &old, 10));
    assert_eq!(
        registry
            .prepare_rotation("node-a", 1, next.clone())
            .unwrap(),
        2
    );
    assert!(!registry.accepts("node-a", &next, 10));
    assert_eq!(registry.activate_rotation("node-a", 2, 20, 10).unwrap(), 3);
    assert!(registry.accepts("node-a", &old, 25));
    assert!(registry.accepts("node-a", &next, 25));
    assert!(!registry.accepts("node-a", &old, 30));
    assert!(registry.accepts("node-a", &next, 30));
    assert!(
        registry
            .prepare_rotation("node-a", 2, "c".repeat(64))
            .is_err()
    );
    assert_eq!(registry.revoke("node-a", 3).unwrap(), 4);
    assert!(!registry.accepts("node-a", &next, 31));
}

/// Tenant, domain, digest and revision identity cannot be silently overwritten.
#[test]
fn registry_rejects_conflicting_registration_and_invalid_profiles() {
    let mut registry = NodeIdentityRegistry::new("test-registry");
    registry
        .register("node-a", "tenant-a", "domain-a", "a".repeat(64), 0)
        .unwrap();
    assert!(
        registry
            .register("node-a", "tenant-b", "domain-b", "b".repeat(64), 0)
            .is_err()
    );
    assert!(registry.accepts("node-a", &"a".repeat(64), 0));
    assert!(
        registry
            .prepare_rotation("node-a", 99, "b".repeat(64))
            .is_err()
    );
    let mut invalid = registry.clone();
    invalid.schema = "old".into();
    assert!(invalid.validate().is_err());
}

/// Every command is atomic, idempotent and bound to its original authenticated administrator.
#[test]
fn commands_are_durable_atomic_and_deduplicated() {
    let mut registry = NodeIdentityRegistry::new("registry");
    let register = crate::IdentityCommand {
        command_id: "enrol-1".into(),
        node_id: "node-1".into(),
        expected_revision: 0,
        action: crate::IdentityAction::Register {
            tenant_id: "tenant".into(),
            domain_id: "domain".into(),
            digest: "a".repeat(64),
            expires_at_ms: 1000,
        },
    };
    let original = registry
        .apply(&register, 1, "sha256:admin-certificate")
        .unwrap();
    assert_eq!(original.revision, 1);
    assert_eq!(
        registry
            .apply(&register, 99, "sha256:another-admin")
            .unwrap(),
        original
    );
    assert_eq!(registry.commands.len(), 1);
    let before = registry.clone();
    let mut conflict = register.clone();
    conflict.expected_revision = 2;
    assert!(registry.apply(&conflict, 2, "admin").is_err());
    assert_eq!(registry, before);
    let invalid = crate::IdentityCommand {
        command_id: "bad-activation".into(),
        node_id: "node-1".into(),
        expected_revision: 1,
        action: crate::IdentityAction::Activate {
            overlap_ms: u64::MAX,
        },
    };
    assert!(registry.apply(&invalid, 5, "admin").is_err());
    assert_eq!(registry, before);
    let restored: NodeIdentityRegistry =
        serde_json::from_slice(&serde_json::to_vec(&registry).unwrap()).unwrap();
    restored.validate().unwrap();
    assert_eq!(restored.commands["enrol-1"], original);
    assert!(!restored.accepts("node-1", &"a".repeat(64), 1000));
}

/// Reenrolment retains tenant/domain and history while refusing every retired certificate.
#[test]
fn reenrolment_cannot_reuse_revoked_leaves_or_change_owner() {
    let mut registry = NodeIdentityRegistry::new("registry");
    registry
        .register("node", "tenant", "domain", "a".repeat(64), 0)
        .unwrap();
    registry.revoke("node", 1).unwrap();
    let mut command = IdentityCommand {
        command_id: "reenrol".into(),
        node_id: "node".into(),
        expected_revision: 2,
        action: IdentityAction::Reenroll {
            digest: "a".repeat(64),
            expires_at_ms: 100,
        },
    };
    assert!(registry.apply(&command, 1, "admin").is_err());
    command.action = IdentityAction::Reenroll {
        digest: "b".repeat(64),
        expires_at_ms: 100,
    };
    registry.apply(&command, 1, "admin").unwrap();
    assert_eq!(registry.records["node"].tenant_id, "tenant");
    assert_eq!(registry.records["node"].domain_id, "domain");
    assert!(registry.accepts("node", &"b".repeat(64), 2));
    assert!(!registry.accepts("node", &"a".repeat(64), 2));
    assert!(
        registry
            .register("other", "tenant", "domain", "a".repeat(64), 2)
            .is_err()
    );
}
