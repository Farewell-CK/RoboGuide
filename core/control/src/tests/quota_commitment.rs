/// Installs a canonical test quota through the same complete policy API used by operator commands.
fn install_quota_limit(control: &mut ControlPlane, tenant_id: &str, kind: ResourceKind, max_units: u64) {
    control
        .accept_resource_quota_registry(
            domain::ResourceQuotaRegistry {
                registry_id: "test-resource-quotas".into(),
                revision: 1,
                limits: vec![domain::ResourceQuotaLimit {
                    tenant_id: tenant_id.into(),
                    kind,
                    max_units,
                }],
            },
            0,
        )
        .expect("explicit tenant quota policy installs");
}

/// Tracks logical reservation claims without creating or registering a simulated Node.
#[test]
fn resource_claims_enforce_tenant_quota_and_survive_checkpoint() {
    let mission = domain::MissionId::new("quota-mission").unwrap();
    let task = TaskRef::new(mission.clone(), TaskId::new("quota-task").unwrap());
    let first_resource = ResourceId::new("quota-resource-first").unwrap();
    let second_resource = ResourceId::new("quota-resource-second").unwrap();
    let mut control = ControlPlane::new();
    control
        .register_mission_tenant(mission.clone(), "tenant-a")
        .unwrap();
    install_quota_limit(&mut control, "tenant-a", ResourceKind::Compute, 3);
    control.reservations.insert(
        first_resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: RoleId::new("worker").unwrap(),
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task.clone()),
        },
    );
    control
        .commit_resource_quotas(
            &mission,
            vec![(first_resource.clone(), ResourceKind::Compute, 2)],
        )
        .unwrap();
    assert_eq!(
        control.quota_usage("tenant-a", ResourceKind::Compute),
        (2, Some(1))
    );
    control.reservations.insert(
        second_resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: RoleId::new("worker").unwrap(),
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task.clone()),
        },
    );
    assert!(matches!(
        control.commit_resource_quotas(
            &mission,
            vec![(second_resource.clone(), ResourceKind::Compute, 2)],
        ),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("quota exceeded")
    ));
    assert!(control.quota_ledger().has_resource_claim(first_resource.as_str()));
    assert!(!control.quota_ledger().has_resource_claim(second_resource.as_str()));
    control.reservations.remove(&second_resource);

    let checkpoint_json = serde_json::to_value(control.checkpoint()).unwrap();
    let mut repinned = checkpoint_json;
    let reservation_json = repinned
        .get_mut("reservations")
        .and_then(serde_json::Value::as_object_mut)
        .and_then(|reservations| reservations.values_mut().next())
        .unwrap();
    reservation_json["task_ref"]["mission_id"] = serde_json::json!("foreign-mission");
    assert!(ControlPlane::restore(serde_json::from_value(repinned).unwrap()).is_err());
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored.quota_usage("tenant-a", ResourceKind::Compute),
        (2, Some(1))
    );
    control.release_resource_quota(&first_resource);
    control.reservations.remove(&first_resource);
    assert_eq!(
        control.quota_usage("tenant-a", ResourceKind::Compute),
        (0, Some(3))
    );
    control.reservations.insert(
        second_resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: RoleId::new("worker").unwrap(),
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task),
        },
    );
    control
        .commit_resource_quotas(
            &mission,
            vec![(second_resource.clone(), ResourceKind::Compute, 2)],
        )
        .unwrap();
    assert_eq!(
        control.quota_usage("tenant-a", ResourceKind::Compute),
        (2, Some(1))
    );
    let lower_limit = domain::ResourceQuotaRegistry {
        registry_id: "test-resource-quotas".into(),
        revision: 2,
        limits: vec![domain::ResourceQuotaLimit {
            tenant_id: "tenant-a".into(),
            kind: ResourceKind::Compute,
            max_units: 1,
        }],
    };
    assert!(control
        .accept_resource_quota_registry(lower_limit, 1)
        .is_err());
    assert_eq!(
        control.resource_quota_registry().unwrap().revision,
        1,
        "rejected policy update must preserve current authority"
    );
    let exact_limit = domain::ResourceQuotaRegistry {
        registry_id: "test-resource-quotas".into(),
        revision: 2,
        limits: vec![domain::ResourceQuotaLimit {
            tenant_id: "tenant-a".into(),
            kind: ResourceKind::Compute,
            max_units: 2,
        }],
    };
    control
        .accept_resource_quota_registry(exact_limit, 1)
        .unwrap();
    assert_eq!(
        control.quota_usage("tenant-a", ResourceKind::Compute),
        (2, Some(0))
    );
}

/// Keeps pre-quota reservations unavailable until their owning lifecycle releases them.
#[test]
fn legacy_reservations_do_not_receive_fabricated_quota_units() {
    let task_ref = TaskRef::new(
        domain::MissionId::new("legacy-quota-mission").unwrap(),
        TaskId::new("legacy-quota-task").unwrap(),
    );
    let role = RoleId::new("worker").unwrap();
    let resource = ResourceId::new("legacy-resource").unwrap();
    let mut legacy = ControlPlane::new();
    legacy.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task_ref.clone(),
            role_id: role,
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task_ref),
        },
    );
    legacy
        .quota_ledger
        .track(QuotaClaim {
            claim_id: crate::quota::resource_claim_id(resource.as_str()),
            tenant_id: "local".into(),
            kind: ResourceKind::Compute,
            units: 5,
            resource_id: None,
        })
        .unwrap();
    let mut legacy_json = serde_json::to_value(legacy.checkpoint()).unwrap();
    legacy_json
        .as_object_mut()
        .unwrap()
        .remove("quota_unaccounted_resources");
    let old_checkpoint = serde_json::from_value(legacy_json).unwrap();
    let mut restored = ControlPlane::restore(old_checkpoint).unwrap();
    let mut unrelated_tenant = restored.clone();
    unrelated_tenant
        .accept_resource_quota_registry(
            domain::ResourceQuotaRegistry {
                registry_id: "unrelated-tenant-quotas".into(),
                revision: 1,
                limits: vec![domain::ResourceQuotaLimit {
                    tenant_id: "tenant-b".into(),
                    kind: ResourceKind::Compute,
                    max_units: 10,
                }],
            },
            0,
        )
        .unwrap();
    let other_mission = domain::MissionId::new("other-quota-mission").unwrap();
    unrelated_tenant
        .register_mission_tenant(other_mission.clone(), "tenant-b")
        .unwrap();
    assert!(unrelated_tenant
        .validate_resource_quotas(
            &other_mission,
            &[(ResourceId::new("tenant-b-resource").unwrap(), ResourceKind::Compute, 1)],
        )
        .is_ok());
    let quota_policy = domain::ResourceQuotaRegistry {
        registry_id: "test-resource-quotas".into(),
        revision: 1,
        limits: vec![domain::ResourceQuotaLimit {
            tenant_id: "local".into(),
            kind: ResourceKind::Compute,
            max_units: 10,
        }],
    };
    assert!(restored
        .accept_resource_quota_registry(quota_policy.clone(), 0)
        .is_err());
    restored.release_resource_quota(&resource);
    restored
        .accept_resource_quota_registry(quota_policy, 0)
        .unwrap();
    assert_eq!(
        restored.quota_usage("local", ResourceKind::Compute),
        (5, Some(5)),
        "legacy opaque claims remain independent; the migration never infers resource linkage"
    );
}

/// Refuses to guess the tenant of a legacy reservation when identity authority is enabled.
#[test]
fn identity_profile_fences_legacy_reservations_with_unknown_tenant() {
    let task_ref = TaskRef::new(
        domain::MissionId::new("unknown-tenant-mission").unwrap(),
        TaskId::new("unknown-tenant-task").unwrap(),
    );
    let role = RoleId::new("worker").unwrap();
    let resource = ResourceId::new("unknown-tenant-resource").unwrap();
    let mut control = ControlPlane::new();
    control
        .bootstrap_node_identity_registry(domain::NodeIdentityRegistry::new("empty-deployment"))
        .unwrap();
    control.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task_ref.clone(),
            role_id: role,
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task_ref),
        },
    );
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    let policy = domain::ResourceQuotaRegistry {
        registry_id: "identity-profile-quotas".into(),
        revision: 1,
        limits: vec![domain::ResourceQuotaLimit {
            tenant_id: "tenant-a".into(),
            kind: ResourceKind::Compute,
            max_units: 10,
        }],
    };
    assert!(restored
        .accept_resource_quota_registry(policy.clone(), 0)
        .is_err());
    restored.release_resource_quota(&resource);
    restored
        .accept_resource_quota_registry(policy, 0)
        .unwrap();
}

/// Preserves old direct quota limits until an administrator explicitly adopts them as revision one.
#[test]
fn legacy_limits_require_exact_policy_adoption_after_restore() {
    let mut legacy = ControlPlane::new();
    legacy
        .quota_ledger
        .set_limit("local", ResourceKind::Compute, 10)
        .unwrap();
    let mut restored = ControlPlane::restore(legacy.checkpoint()).unwrap();
    let adopted = domain::ResourceQuotaRegistry {
        registry_id: "adopted-legacy-quota".into(),
        revision: 1,
        limits: vec![domain::ResourceQuotaLimit {
            tenant_id: "local".into(),
            kind: ResourceKind::Compute,
            max_units: 10,
        }],
    };
    let changed = domain::ResourceQuotaRegistry {
        limits: vec![domain::ResourceQuotaLimit {
            max_units: 12,
            ..adopted.limits[0].clone()
        }],
        ..adopted.clone()
    };
    assert!(restored
        .accept_resource_quota_registry(changed, 0)
        .is_err());
    restored
        .accept_resource_quota_registry(adopted, 0)
        .unwrap();
    assert_eq!(
        restored.quota_usage("local", ResourceKind::Compute),
        (0, Some(10))
    );
}
