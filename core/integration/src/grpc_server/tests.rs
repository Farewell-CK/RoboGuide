use super::session::{validate_registration, validate_state_observation_batch};
use super::*;

/// Registration accepts static local/global provider maxima and rejects a concrete Group scope.
#[test]
fn registration_rejects_execution_group_memory_provider_scope() {
    let mut registration = crate::grpc::v0_4::NodeRegistration {
        node_id: "dog-a".to_string(),
        local_systems: vec![crate::grpc::v0_4::LocalSystemDescriptor {
            id: "memory".to_string(),
            runtime: Some(crate::grpc::v0_4::LocalRuntime {
                name: "memory-runtime".to_string(),
                version: "1".to_string(),
            }),
            metadata: Default::default(),
        }],
        capabilities: Vec::new(),
        sensors: Vec::new(),
        resources: Vec::new(),
        metadata: Default::default(),
        node_contract_version: LEGACY_NODE_CONTRACT_VERSION.to_string(),
        state_exports: Vec::new(),
        memory_providers: vec![crate::grpc::v0_4::MemoryProviderDescriptor {
            provider_id: "experience".to_string(),
            local_system_id: "memory".to_string(),
            kind: crate::grpc::v0_4::MemoryKind::Experience as i32,
            scope: crate::grpc::v0_4::MemoryScopeKind::Global as i32,
            execution_group_id: String::new(),
            visibility: crate::grpc::v0_4::MemoryVisibility::Discoverable as i32,
            payload_schema: "example.experience/v1".to_string(),
            media_type: "application/json".to_string(),
        }],
        capability_profiles: Vec::new(),
        operation_support: Vec::new(),
    };
    validate_registration(&registration).expect("global provider maximum should be valid");

    registration.memory_providers[0].scope =
        crate::grpc::v0_4::MemoryScopeKind::ExecutionGroup as i32;
    registration.memory_providers[0].execution_group_id = "group-a".to_string();
    let error = validate_registration(&registration)
        .expect_err("static execution Group provider scope should be rejected");
    assert_eq!(error.code(), tonic::Code::InvalidArgument);
}

/// Node Contract v0.6 requires independent typed profiles and canonical operation support.
#[test]
fn current_registration_requires_profiles_and_operation_support() {
    let mut registration = crate::grpc::v0_4::NodeRegistration {
        node_id: "arm-a".to_string(),
        local_systems: vec![crate::grpc::v0_4::LocalSystemDescriptor {
            id: "manipulator".to_string(),
            runtime: Some(crate::grpc::v0_4::LocalRuntime {
                name: "local-eaios".to_string(),
                version: "1".to_string(),
            }),
            metadata: Default::default(),
        }],
        node_contract_version: NODE_CONTRACT_VERSION.to_string(),
        capability_profiles: vec![crate::grpc::v0_4::CapabilityProfile {
            contract: "manipulation.grasp@v1".to_string(),
            kind: "mobility".to_string(),
            local_system_id: "manipulator".to_string(),
            ready: true,
            attributes: std::collections::HashMap::from([(
                "max-payload-g".to_string(),
                crate::grpc::v0_4::ScalarValue {
                    value: Some(crate::grpc::v0_4::scalar_value::Value::IntegerValue(5_000)),
                },
            )]),
        }],
        operation_support: vec![crate::grpc::v0_4::OperationSupport {
            operation: Some(crate::grpc::v0_4::OperationRef {
                namespace: "object".to_string(),
                name: "relocate".to_string(),
                version: "v1".to_string(),
            }),
            local_system_id: "manipulator".to_string(),
        }],
        ..Default::default()
    };
    validate_registration(&registration).expect("current profile should be accepted");

    let operation_support = std::mem::take(&mut registration.operation_support);
    assert_eq!(
        validate_registration(&registration)
            .expect_err("current contract requires explicit operation support")
            .code(),
        tonic::Code::InvalidArgument
    );
    registration.operation_support = operation_support;

    registration
        .capabilities
        .push(crate::grpc::v0_4::Capability {
            kind: "mobility".to_string(),
            available: true,
            contracts: vec!["manipulation.grasp@v1".to_string()],
            local_system_id: "manipulator".to_string(),
        });
    assert_eq!(
        validate_registration(&registration)
            .expect_err("current contract cannot mix legacy capabilities")
            .code(),
        tonic::Code::InvalidArgument
    );

    registration.node_contract_version = LEGACY_NODE_CONTRACT_VERSION.to_string();
    registration.capabilities.clear();
    assert_eq!(
        validate_registration(&registration)
            .expect_err("legacy contract cannot acquire current profile fields")
            .code(),
        tonic::Code::InvalidArgument
    );
}

/// v0.5 compatibility cannot infer operation support from a capability profile.
#[test]
fn previous_profile_contract_rejects_new_operation_support_field() {
    let mut registration = crate::grpc::v0_4::NodeRegistration {
        node_id: "arm-a".to_string(),
        local_systems: vec![crate::grpc::v0_4::LocalSystemDescriptor {
            id: "manipulator".to_string(),
            runtime: Some(crate::grpc::v0_4::LocalRuntime {
                name: "local-eaios".to_string(),
                version: "1".to_string(),
            }),
            metadata: Default::default(),
        }],
        node_contract_version: PREVIOUS_NODE_CONTRACT_VERSION.to_string(),
        capability_profiles: vec![crate::grpc::v0_4::CapabilityProfile {
            contract: "object.relocate@v1".to_string(),
            kind: "transport".to_string(),
            local_system_id: "manipulator".to_string(),
            ready: true,
            attributes: Default::default(),
        }],
        operation_support: vec![crate::grpc::v0_4::OperationSupport {
            operation: Some(crate::grpc::v0_4::OperationRef {
                namespace: "object".to_string(),
                name: "relocate".to_string(),
                version: "v1".to_string(),
            }),
            local_system_id: "manipulator".to_string(),
        }],
        ..Default::default()
    };

    assert_eq!(
        validate_registration(&registration)
            .expect_err("v0.5 cannot silently gain v0.6 semantics")
            .code(),
        tonic::Code::InvalidArgument
    );
    registration.operation_support.clear();
    validate_registration(&registration).expect("original v0.5 profile remains compatible");
}

/// Each negotiated Node Contract accepts exactly one capability and invocation representation.
#[test]
fn node_contract_versions_reject_mixed_or_downgraded_semantics() {
    let current = crate::grpc::v0_4::CanonicalInvocation {
        mission_id: "m".to_string(),
        task_id: "t".to_string(),
        group_id: "g".to_string(),
        role_id: "r".to_string(),
        intent: Some(crate::grpc::v0_4::ExecutionIntent {
            operation: Some(crate::grpc::v0_4::OperationRef {
                namespace: "compute".to_string(),
                name: "noop".to_string(),
                version: "v1".to_string(),
            }),
            objective: "Exercise the selected compute node".to_string(),
            parameters: Default::default(),
        }),
        ..Default::default()
    };
    invocation_for_contract(current.clone(), NODE_CONTRACT_VERSION)
        .expect("current contract accepts semantic intent");
    assert_eq!(
        invocation_for_contract(current, LEGACY_NODE_CONTRACT_VERSION)
            .expect_err("legacy route cannot silently discard the objective")
            .code(),
        tonic::Code::FailedPrecondition
    );

    let legacy = crate::grpc::v0_4::CanonicalInvocation {
        mission_id: "m".to_string(),
        task_id: "t".to_string(),
        group_id: "g".to_string(),
        role_id: "r".to_string(),
        capability_contract: "compute.noop@v1".to_string(),
        ..Default::default()
    };
    invocation_for_contract(legacy.clone(), LEGACY_NODE_CONTRACT_VERSION)
        .expect("legacy contract retains its original invocation form");
    assert_eq!(
        invocation_for_contract(legacy, NODE_CONTRACT_VERSION)
            .expect_err("current contract cannot mix in legacy invocation fields")
            .code(),
        tonic::Code::InvalidArgument
    );

    let compatible = crate::grpc::v0_4::CanonicalInvocation {
        mission_id: "m".to_string(),
        task_id: "t".to_string(),
        group_id: "g".to_string(),
        role_id: "r".to_string(),
        intent: Some(crate::grpc::v0_4::ExecutionIntent {
            operation: Some(crate::grpc::v0_4::OperationRef {
                namespace: "compute".to_string(),
                name: "noop".to_string(),
                version: "v1".to_string(),
            }),
            objective: "compute.noop@v1".to_string(),
            parameters: Default::default(),
        }),
        ..Default::default()
    };
    let compatible = invocation_for_contract(compatible, LEGACY_NODE_CONTRACT_VERSION)
        .expect("legacy-equivalent semantic intent downgrades explicitly");
    assert_eq!(compatible.capability_contract, "compute.noop@v1");
    assert!(compatible.intent.is_none());
}

/// Builds one registered-export set for State batch validation tests.
fn state_exports() -> BTreeSet<String> {
    BTreeSet::from(["hazard-state".to_string(), "contact-state".to_string()])
}

/// A bounded batch may carry independent valid JSON observations for registered exports.
#[test]
fn state_batch_accepts_registered_bounded_json() {
    let batch = crate::grpc::v0_4::StateObservationBatch {
        session_id: "session-a".to_string(),
        sequence: 2,
        observations: vec![
            crate::grpc::v0_4::StateObservation {
                export_id: "hazard-state".to_string(),
                json_value: br#"{"present":true}"#.to_vec(),
                has_source_observed_at: true,
                source_observed_at_ms: 10,
                has_confidence: true,
                confidence_millionths: 900_000,
            },
            crate::grpc::v0_4::StateObservation {
                export_id: "contact-state".to_string(),
                json_value: br#"{"connected":false}"#.to_vec(),
                has_source_observed_at: false,
                source_observed_at_ms: 0,
                has_confidence: false,
                confidence_millionths: 0,
            },
        ],
    };

    validate_state_observation_batch(&batch, &state_exports())
        .expect("registered bounded observations should be accepted");
}

/// State batches cannot smuggle undeclared channels or duplicate one channel in a batch.
#[test]
fn state_batch_rejects_undeclared_and_duplicate_exports() {
    let observation = crate::grpc::v0_4::StateObservation {
        export_id: "unknown-state".to_string(),
        json_value: b"true".to_vec(),
        has_source_observed_at: false,
        source_observed_at_ms: 0,
        has_confidence: false,
        confidence_millionths: 0,
    };
    let undeclared = crate::grpc::v0_4::StateObservationBatch {
        session_id: "session-a".to_string(),
        sequence: 2,
        observations: vec![observation],
    };
    assert_eq!(
        validate_state_observation_batch(&undeclared, &state_exports())
            .expect_err("undeclared export should be rejected")
            .code(),
        tonic::Code::InvalidArgument
    );

    let observation = crate::grpc::v0_4::StateObservation {
        export_id: "hazard-state".to_string(),
        json_value: b"true".to_vec(),
        has_source_observed_at: false,
        source_observed_at_ms: 0,
        has_confidence: false,
        confidence_millionths: 0,
    };
    let duplicate = crate::grpc::v0_4::StateObservationBatch {
        session_id: "session-a".to_string(),
        sequence: 2,
        observations: vec![observation.clone(), observation],
    };
    assert_eq!(
        validate_state_observation_batch(&duplicate, &state_exports())
            .expect_err("duplicate export should be rejected")
            .code(),
        tonic::Code::InvalidArgument
    );
}

/// Invalid JSON, oversized payloads, and invalid confidence fail at the protocol boundary.
#[test]
fn state_batch_rejects_invalid_payload_bounds() {
    for (json_value, has_confidence, confidence) in [
        (b"not-json".to_vec(), false, 0),
        (vec![b' '; 64 * 1024 + 1], false, 0),
        (b"true".to_vec(), true, 1_000_001),
    ] {
        let batch = crate::grpc::v0_4::StateObservationBatch {
            session_id: "session-a".to_string(),
            sequence: 2,
            observations: vec![crate::grpc::v0_4::StateObservation {
                export_id: "hazard-state".to_string(),
                json_value,
                has_source_observed_at: false,
                source_observed_at_ms: 0,
                has_confidence,
                confidence_millionths: confidence,
            }],
        };
        assert_eq!(
            validate_state_observation_batch(&batch, &state_exports())
                .expect_err("invalid State payload should be rejected")
                .code(),
            tonic::Code::InvalidArgument
        );
    }
}

/// Peer readiness is admitted only as one bounded fact on the current management sequence.
#[test]
fn peer_readiness_requires_current_session_identity_and_bounds() {
    let router = GrpcNodeRouter::default();
    let (sender, _receiver) = mpsc::unbounded_channel();
    router.sessions.lock().expect("registry lock").insert(
        "dog-a".to_string(),
        RoutedSession {
            session_id: "session-current".to_string(),
            sender,
            lease_id: "lease-current".to_string(),
            last_heartbeat: std::time::Instant::now(),
            lease_duration: std::time::Duration::from_secs(15),
            node_contract_version: LEGACY_NODE_CONTRACT_VERSION.to_string(),
            management_sequence: 1,
            state_export_ids: BTreeSet::new(),
            active: true,
        },
    );
    let readiness = |session_id: &str, sequence: u64, valid_for_ms: u64| NodeMessage {
        message: Some(NodePayload::PeerChannelReadiness(
            crate::grpc::v0_4::PeerChannelReadiness {
                session_id: session_id.to_string(),
                sequence,
                group_id: "group-guidance".to_string(),
                context_id: "guidance".to_string(),
                context_role_id: "guide".to_string(),
                channel_instance_id: "channel-1".to_string(),
                profile_id: "guidance-peer".to_string(),
                message_schema: "guidance/v1".to_string(),
                ready: true,
                valid_for_ms,
                local_system_id: "motion".to_string(),
            },
        )),
    };

    assert!(
        accept_current_message(
            &router,
            "dog-a",
            "session-current",
            &readiness("session-current", 2, 5_000),
        )
        .expect("valid peer readiness is admitted")
    );
    assert!(
        !accept_current_message(
            &router,
            "dog-a",
            "session-current",
            &readiness("session-current", 2, 5_000),
        )
        .expect("duplicate sequence is ignored")
    );
    assert!(
        !accept_current_message(
            &router,
            "dog-a",
            "session-current",
            &readiness("session-other", 3, 5_000),
        )
        .expect("wrong payload session is ignored")
    );
    assert_eq!(
        accept_current_message(
            &router,
            "dog-a",
            "session-current",
            &readiness("session-current", 3, 0),
        )
        .expect_err("zero validity is rejected")
        .code(),
        tonic::Code::InvalidArgument
    );
}

/// Transport emits success only after application authority explicitly accepts the fact.
#[tokio::test]
async fn fact_delivery_waits_for_application_acceptance() {
    let (events, mut receiver) = mpsc::unbounded_channel();
    let delivery = tokio::spawn(async move {
        deliver_for_acceptance(
            &events,
            GrpcNodeEvent::Unavailable {
                node_id: "dog-a".to_string(),
                session_id: "session-a".to_string(),
            },
        )
        .await
    });
    let event = receiver.recv().await.expect("fact delivery exists");
    assert!(!delivery.is_finished());
    let (_event, completion) = event.into_parts();
    completion.accept();
    delivery
        .await
        .expect("delivery task joins")
        .expect("application acceptance reaches transport");
}

/// Application rejection is returned as a protocol failure instead of a false acknowledgement.
#[tokio::test]
async fn fact_delivery_preserves_application_rejection() {
    let (events, mut receiver) = mpsc::unbounded_channel();
    let delivery = tokio::spawn(async move {
        deliver_for_acceptance(
            &events,
            GrpcNodeEvent::Unavailable {
                node_id: "dog-a".to_string(),
                session_id: "session-a".to_string(),
            },
        )
        .await
    });
    let event = receiver.recv().await.expect("fact delivery exists");
    let (_event, completion) = event.into_parts();
    completion.reject("resource conflict");
    let error = delivery
        .await
        .expect("delivery task joins")
        .expect_err("application rejection reaches transport");
    assert_eq!(error.code(), tonic::Code::FailedPrecondition);
    assert!(error.message().contains("resource conflict"));
}

/// Application infrastructure failure stays retryable instead of becoming a fact rejection.
#[tokio::test]
async fn fact_delivery_preserves_application_unavailability() {
    let (events, mut receiver) = mpsc::unbounded_channel();
    let delivery = tokio::spawn(async move {
        deliver_for_acceptance(
            &events,
            GrpcNodeEvent::Unavailable {
                node_id: "dog-a".to_string(),
                session_id: "session-a".to_string(),
            },
        )
        .await
    });
    let event = receiver.recv().await.expect("fact delivery exists");
    let (_event, completion) = event.into_parts();
    completion.unavailable("checkpoint store is offline");
    let error = delivery
        .await
        .expect("delivery task joins")
        .expect_err("application unavailability reaches transport");
    assert_eq!(error.code(), tonic::Code::Unavailable);
    assert!(error.message().contains("checkpoint store is offline"));
}

/// A session cannot receive commands before Controller application registration acceptance.
#[test]
fn pending_registration_cannot_route_commands() {
    let router = GrpcNodeRouter::default();
    let (sender, _receiver) = mpsc::unbounded_channel();
    router.sessions.lock().expect("registry lock").insert(
        "dog-a".to_string(),
        RoutedSession {
            session_id: "session-pending".to_string(),
            sender,
            lease_id: "lease-pending".to_string(),
            last_heartbeat: std::time::Instant::now(),
            lease_duration: std::time::Duration::from_secs(15),
            node_contract_version: LEGACY_NODE_CONTRACT_VERSION.to_string(),
            management_sequence: 0,
            state_export_ids: BTreeSet::new(),
            active: false,
        },
    );
    let error = router
        .execute(
            "dog-a",
            "command-1".to_string(),
            "execution-1".to_string(),
            crate::grpc::v0_4::CanonicalInvocation {
                mission_id: "m".to_string(),
                task_id: "t".to_string(),
                group_id: "g".to_string(),
                role_id: "r".to_string(),
                capability_contract: "compute.noop@v1".to_string(),
                parameters: Default::default(),
                intent: None,
            },
            Vec::new(),
        )
        .expect_err("pending route rejects commands");
    assert_eq!(error.code(), tonic::Code::Unavailable);
    assert!(error.message().contains("pending"));
}

/// Expired leases cannot route Execute or Cancel.
#[test]
fn expired_lease_rejects_new_commands() {
    let router = GrpcNodeRouter::default();
    let (sender, _receiver) = mpsc::unbounded_channel();
    router.sessions.lock().expect("registry lock").insert(
        "dog-a".to_string(),
        RoutedSession {
            session_id: "session-old".to_string(),
            sender,
            lease_id: "lease-old".to_string(),
            last_heartbeat: std::time::Instant::now() - std::time::Duration::from_secs(2),
            lease_duration: std::time::Duration::from_secs(1),
            node_contract_version: LEGACY_NODE_CONTRACT_VERSION.to_string(),
            management_sequence: 0,
            state_export_ids: BTreeSet::new(),
            active: true,
        },
    );
    assert_eq!(
        router
            .execute(
                "dog-a",
                "command-1".to_string(),
                "execution-1".to_string(),
                crate::grpc::v0_4::CanonicalInvocation {
                    mission_id: "m".to_string(),
                    task_id: "t".to_string(),
                    group_id: "g".to_string(),
                    role_id: "r".to_string(),
                    capability_contract: "compute.noop@v1".to_string(),
                    parameters: Default::default(),
                    intent: None,
                },
                Vec::new(),
            )
            .expect_err("expired route rejected")
            .code(),
        tonic::Code::Unavailable
    );
}

/// Memory data-plane callers can identify only the active, unexpired route for one Node.
#[test]
fn current_session_check_rejects_pending_wrong_and_expired_routes() {
    let router = GrpcNodeRouter::default();
    let (sender, _receiver) = mpsc::unbounded_channel();
    router.sessions.lock().expect("registry lock").insert(
        "dog-a".to_string(),
        RoutedSession {
            session_id: "session-current".to_string(),
            sender,
            lease_id: "lease-current".to_string(),
            last_heartbeat: std::time::Instant::now(),
            lease_duration: std::time::Duration::from_secs(15),
            node_contract_version: LEGACY_NODE_CONTRACT_VERSION.to_string(),
            management_sequence: 0,
            state_export_ids: BTreeSet::new(),
            active: false,
        },
    );
    assert!(
        !router
            .session_is_current("dog-a", "session-current")
            .expect("pending route can be inspected")
    );

    {
        let mut sessions = router.sessions.lock().expect("registry lock");
        sessions.get_mut("dog-a").expect("route exists").active = true;
    }
    assert!(
        router
            .session_is_current("dog-a", "session-current")
            .expect("active route can be inspected")
    );
    assert!(
        !router
            .session_is_current("dog-a", "session-old")
            .expect("stale identity can be inspected")
    );
    assert!(
        !router
            .session_is_current("dog-b", "session-current")
            .expect("unknown Node can be inspected")
    );

    {
        let mut sessions = router.sessions.lock().expect("registry lock");
        let route = sessions.get_mut("dog-a").expect("route exists");
        route.last_heartbeat = std::time::Instant::now() - std::time::Duration::from_secs(16);
    }
    assert!(
        !router
            .session_is_current("dog-a", "session-current")
            .expect("expired route can be inspected")
    );
}

/// A late message from a fenced session cannot refresh the current route.
#[test]
fn newer_session_fences_late_old_heartbeat() {
    let router = GrpcNodeRouter::default();
    let (sender, _receiver) = mpsc::unbounded_channel();
    router.sessions.lock().expect("registry lock").insert(
        "dog-a".to_string(),
        RoutedSession {
            session_id: "session-new".to_string(),
            sender,
            lease_id: "lease-new".to_string(),
            last_heartbeat: std::time::Instant::now(),
            lease_duration: std::time::Duration::from_secs(15),
            node_contract_version: LEGACY_NODE_CONTRACT_VERSION.to_string(),
            management_sequence: 0,
            state_export_ids: BTreeSet::new(),
            active: true,
        },
    );
    let message = NodeMessage {
        message: Some(NodePayload::Heartbeat(crate::grpc::v0_4::Heartbeat {
            session_id: "session-old".to_string(),
            lease_id: "lease-old".to_string(),
            sequence: 10,
            status: None,
        })),
    };
    assert!(
        !accept_current_message(&router, "dog-a", "session-old", &message)
            .expect("message validates safely")
    );
}
