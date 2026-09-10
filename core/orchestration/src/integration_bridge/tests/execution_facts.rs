//! Wire conversion and execution-fact fencing tests.

use super::dispatch_recovery::{execution_snapshot, execution_snapshot_with_phase};
use super::*;

/// Wire conversion cannot reintroduce a live Group identity into static provider metadata.
#[test]
fn memory_provider_conversion_rejects_execution_group_scope() {
    let wire = integration::grpc::v0_4::MemoryProviderDescriptor {
        provider_id: "experience".to_string(),
        local_system_id: "memory".to_string(),
        kind: integration::grpc::v0_4::MemoryKind::Experience as i32,
        scope: integration::grpc::v0_4::MemoryScopeKind::ExecutionGroup as i32,
        execution_group_id: "group-a".to_string(),
        visibility: integration::grpc::v0_4::MemoryVisibility::Discoverable as i32,
        payload_schema: "example.experience/v1".to_string(),
        media_type: "application/json".to_string(),
    };

    assert!(matches!(
        memory_provider_from_wire(&wire),
        Err(IntegrationRuntimeError::Protocol(reason))
            if reason.contains("cannot contain an execution Group")
    ));
}

/// Conversion preserves exact readiness when sibling contracts share a coarse kind.
#[test]
fn registration_conversion_preserves_per_contract_readiness() {
    let registration = registration_from_wire(NodeRegistration {
        node_id: "dog-a".to_string(),
        local_systems: vec![LocalSystemDescriptor {
            id: "mapping".to_string(),
            runtime: Some(WireRuntime {
                name: "mapping-runtime".to_string(),
                version: "1".to_string(),
            }),
            metadata: Default::default(),
        }],
        capabilities: vec![
            WireCapability {
                kind: "compute".to_string(),
                available: true,
                contracts: vec!["spatial.map.build@v0".to_string()],
                local_system_id: "mapping".to_string(),
            },
            WireCapability {
                kind: "compute".to_string(),
                available: false,
                contracts: vec!["spatial.map.localize@v0".to_string()],
                local_system_id: "mapping".to_string(),
            },
            WireCapability {
                kind: "observation".to_string(),
                available: true,
                contracts: vec!["spatial.localization.observe@v0".to_string()],
                local_system_id: "mapping".to_string(),
            },
        ],
        sensors: Vec::new(),
        resources: Vec::new(),
        metadata: Default::default(),
        node_contract_version: "roboguide.node.v0.3".to_string(),
        state_exports: Vec::new(),
        memory_providers: Vec::new(),
    })
    .expect("registration converts");
    let build = parse_contract("spatial.map.build@v0").expect("build contract parses");
    let localize = parse_contract("spatial.map.localize@v0").expect("localize contract parses");
    let observe =
        parse_contract("spatial.localization.observe@v0").expect("observation contract parses");

    assert!(registration.contract_is_available(&build));
    assert!(!registration.contract_is_available(&localize));
    assert!(registration.contract_is_available_for_kind(&observe, CapabilityKind::Observation));
    assert!(!registration.contract_is_available_for_kind(&observe, CapabilityKind::Compute));
    assert!(
        registration
            .capabilities()
            .iter()
            .any(|capability| capability.kind() == CapabilityKind::Compute
                && capability.is_available())
    );
}

/// A RegistrationUpdate changes only later matching decisions for the exact contract.
#[test]
fn readiness_update_changes_later_control_matching() {
    let wire_registration = |available: bool| NodeRegistration {
        node_id: "dog-a".to_string(),
        local_systems: vec![LocalSystemDescriptor {
            id: "mapping".to_string(),
            runtime: Some(WireRuntime {
                name: "mapping-runtime".to_string(),
                version: "1".to_string(),
            }),
            metadata: Default::default(),
        }],
        capabilities: vec![WireCapability {
            kind: "observation".to_string(),
            available,
            contracts: vec!["spatial.localization.verify@v0".to_string()],
            local_system_id: "mapping".to_string(),
        }],
        sensors: Vec::new(),
        resources: Vec::new(),
        metadata: Default::default(),
        node_contract_version: "roboguide.node.v0.3".to_string(),
        state_exports: Vec::new(),
        memory_providers: Vec::new(),
    };
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("readiness-update").expect("correlation is valid");
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "session-a".to_string(),
                lease_id: "lease-a".to_string(),
                registration: wire_registration(false),
            },
            TimestampMs::new(0),
            &correlation,
        )
        .expect("registration is consumed");
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-a".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::Heartbeat(integration::grpc::v0_4::Heartbeat {
                        session_id: "session-a".to_string(),
                        lease_id: "lease-a".to_string(),
                        sequence: 1,
                        status: Some(integration::grpc::v0_4::NodeStatus {
                            health: "online".to_string(),
                            detail: String::new(),
                        }),
                    })),
                },
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("heartbeat is consumed");
    let requirement = domain::TaskRequirement::new(
        domain::MissionId::new("mission-readiness").expect("mission id is valid"),
        domain::TaskId::new("verify-map").expect("task id is valid"),
        vec![domain::RoleRequirement::new_with_actor_and_contract(
            domain::RoleId::new("localizer").expect("role id is valid"),
            domain::ActorId::new("robot").expect("actor id is valid"),
            CapabilityKind::Observation,
            parse_contract("spatial.localization.verify@v0").expect("contract is valid"),
            None,
        )],
    )
    .expect("requirement is valid");
    let mut decision_events = InMemoryEventLog::new();
    assert!(matches!(
        bridge.control().match_capabilities(
            bridge.state(),
            &requirement,
            TimestampMs::new(1),
            &correlation,
            &mut decision_events,
        ),
        Err(control::ControlError::NoCandidate(_))
    ));

    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-a".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::RegistrationUpdate(
                        integration::grpc::v0_4::RegistrationUpdate {
                            session_id: "session-a".to_string(),
                            sequence: 2,
                            registration: Some(wire_registration(true)),
                        },
                    )),
                },
            },
            TimestampMs::new(2),
            &correlation,
        )
        .expect("readiness update is consumed");
    let candidates = bridge
        .control()
        .match_capabilities(
            bridge.state(),
            &requirement,
            TimestampMs::new(2),
            &correlation,
            &mut decision_events,
        )
        .expect("ready contract matches");
    assert_eq!(candidates.roles()[0].node_ids().len(), 1);
}

/// Bound Group assignments are the only source of NodeId for Runtime routing.
#[test]
fn execute_bound_rejects_unbound_group_role() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let group_id = domain::ExecutionGroupId::new("group-unknown").expect("group id valid");
    let role_id = domain::RoleId::new("carrier").expect("role id valid");
    let contract =
        CapabilityContractRef::new("mobility", "reach_region", "v1").expect("contract valid");
    let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
    let correlation = CorrelationId::new("bound-route-test").expect("correlation valid");
    assert!(
        matches!(bridge.execute_bound("execution-1".to_string(), &group_id, &role_id, intent, correlation), Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("unknown"))
    );
}

/// Older execution events cannot regress a newer reconnect snapshot.
#[test]
fn execution_sequence_fences_stale_reconnect_events() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("sequence-test").expect("correlation valid");
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-new".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::ExecutionSnapshot(
                        integration::grpc::v0_4::ExecutionSnapshot {
                            session_id: "session-new".to_string(),
                            execution_id: "execution-1".to_string(),
                            last_sequence: 3,
                            phase: ExecutionPhase::Completed as i32,
                            reason: String::new(),
                        },
                    )),
                },
            },
            TimestampMs::new(3),
            &correlation,
        )
        .expect("snapshot is consumed");
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-new".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::ExecutionEvent(
                        integration::grpc::v0_4::ExecutionEvent {
                            session_id: "session-new".to_string(),
                            execution_id: "execution-1".to_string(),
                            sequence: 2,
                            phase: ExecutionPhase::Started as i32,
                            reason: String::new(),
                        },
                    )),
                },
            },
            TimestampMs::new(4),
            &correlation,
        )
        .expect("stale event is ignored");
    assert_eq!(
        bridge.execution_status("execution-1"),
        Some(RemoteExecutionStatus::Completed)
    );
}

/// A terminal execution fact is immutable even when a conflicting fact has a higher sequence.
#[test]
fn terminal_execution_status_rejects_later_conflict() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("terminal-test").expect("correlation valid");
    bridge
        .consume(
            execution_snapshot("dog-a", "execution-terminal", 3, ExecutionPhase::Completed),
            TimestampMs::new(3),
            &correlation,
        )
        .expect("terminal snapshot is consumed");
    bridge
        .consume(
            execution_snapshot("dog-a", "execution-terminal", 4, ExecutionPhase::Completed),
            TimestampMs::new(4),
            &correlation,
        )
        .expect("same terminal status is idempotent");

    assert!(matches!(
        bridge.consume(
            execution_snapshot("dog-a", "execution-terminal", 5, ExecutionPhase::Failed),
            TimestampMs::new(5),
            &correlation,
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("immutable")
    ));
    assert_eq!(
        bridge.execution_status("execution-terminal"),
        Some(RemoteExecutionStatus::Completed)
    );
}

/// A high-sequence fact from another node cannot fence the execution owner's later fact.
#[test]
fn wrong_node_execution_fact_does_not_poison_sequence() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("wrong-node-test").expect("correlation valid");
    bridge
        .consume(
            execution_snapshot("dog-a", "execution-owned", 1, ExecutionPhase::Started),
            TimestampMs::new(1),
            &correlation,
        )
        .expect("owner snapshot is consumed");

    assert!(matches!(
        bridge.consume(
            execution_snapshot("dog-b", "execution-owned", 100, ExecutionPhase::Failed),
            TimestampMs::new(2),
            &correlation,
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("owner")
    ));
    bridge
        .consume(
            execution_snapshot("dog-a", "execution-owned", 2, ExecutionPhase::Completed),
            TimestampMs::new(3),
            &correlation,
        )
        .expect("owner's next fact remains admissible");
    assert_eq!(
        bridge.execution_status("execution-owned"),
        Some(RemoteExecutionStatus::Completed)
    );
}

/// An invalid phase cannot advance the execution sequence before validation succeeds.
#[test]
fn invalid_execution_phase_does_not_poison_sequence() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("invalid-phase-test").expect("correlation valid");
    assert!(matches!(
        bridge.consume(
            execution_snapshot_with_phase("dog-a", "execution-phase", 100, i32::MAX),
            TimestampMs::new(1),
            &correlation,
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("phase")
    ));
    bridge
        .consume(
            execution_snapshot("dog-a", "execution-phase", 1, ExecutionPhase::Started),
            TimestampMs::new(2),
            &correlation,
        )
        .expect("valid lower sequence is accepted after invalid phase");
    assert_eq!(
        bridge.execution_status("execution-phase"),
        Some(RemoteExecutionStatus::Running)
    );
}

/// Unknown execution is persisted and queued for reconciliation without a terminal outcome.
#[test]
fn unknown_execution_emits_recovery_evidence() {
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission").expect("mission valid"),
        domain::TaskId::new("task").expect("task valid"),
        domain::ExecutionGroupId::new("group").expect("group valid"),
        domain::RoleId::new("role").expect("role valid"),
        NodeId::new("dog-a").expect("node valid"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        CorrelationId::new("unknown-test").expect("correlation valid"),
    );
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    bridge
        .runtime
        .record_dispatched("execution-unknown".to_string(), command, Vec::new())
        .expect("dispatch records");

    bridge
        .consume(
            execution_snapshot("dog-a", "execution-unknown", 1, ExecutionPhase::Unknown),
            TimestampMs::new(1),
            &CorrelationId::new("unknown-test").expect("correlation valid"),
        )
        .expect("unknown fact is accepted");

    assert!(bridge.terminal_task_outcomes().is_empty());
    assert!(matches!(
        bridge.take_runtime_events().as_slice(),
        [ExecutionEvent::RecoveryRequired { .. }]
    ));
    assert!(bridge.events.contains_payload(|payload| matches!(
        payload,
        EventPayload::RuntimeExecutionRecoveryRequired { execution_id, .. }
            if execution_id == "execution-unknown"
    )));
}

/// A receipt cannot borrow one Node route while carrying another session provenance.
#[test]
fn command_receipt_requires_exact_admitted_session() {
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission").expect("mission valid"),
        domain::TaskId::new("task").expect("task valid"),
        domain::ExecutionGroupId::new("group").expect("group valid"),
        domain::RoleId::new("role").expect("role valid"),
        NodeId::new("dog-a").expect("node valid"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        CorrelationId::new("receipt-session-test").expect("correlation valid"),
    );
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    bridge
        .runtime
        .prepare_dispatch("attempt-1".to_string(), command, Vec::new())
        .expect("dispatch intent prepares");
    let event = GrpcNodeEvent::NodeMessage {
        node_id: "dog-a".to_string(),
        session_id: "session-current".to_string(),
        message: integration::grpc::v0_4::NodeMessage {
            message: Some(NodePayload::CommandReceipt(
                integration::grpc::v0_4::CommandReceipt {
                    session_id: "session-stale".to_string(),
                    sequence: 1,
                    command_id: "dispatch-attempt-1".to_string(),
                    execution_id: "attempt-1".to_string(),
                    kind: integration::grpc::v0_4::CommandKind::CommandExecute as i32,
                    status: integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted as i32,
                    reason: String::new(),
                },
            )),
        },
    };

    assert!(matches!(
        bridge.consume(
            event,
            TimestampMs::new(1),
            &CorrelationId::new("receipt-session-test").expect("correlation valid"),
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("session")
    ));
    assert_eq!(
        bridge.runtime.pending_dispatch_intents().len(),
        1,
        "rejected provenance must not acknowledge the dispatch"
    );
}

/// A reconnect snapshot without a current Runtime command cannot be silently re-dispatched.
#[test]
fn observed_reconnect_execution_requires_reconciliation_before_route() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("reconnect-command-test").expect("correlation valid");
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-new".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::ExecutionSnapshot(
                        integration::grpc::v0_4::ExecutionSnapshot {
                            session_id: "session-new".to_string(),
                            execution_id: "execution-1".to_string(),
                            last_sequence: 1,
                            phase: ExecutionPhase::Started as i32,
                            reason: String::new(),
                        },
                    )),
                },
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("snapshot is consumed");
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission").expect("mission valid"),
        domain::TaskId::new("task").expect("task valid"),
        domain::ExecutionGroupId::new("group").expect("group valid"),
        domain::RoleId::new("role").expect("role valid"),
        NodeId::new("dog-a").expect("node valid"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        correlation,
    );
    assert!(matches!(
        bridge.execute("execution-1".to_string(), command, Vec::new()),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("reconciliation")
    ));
}
