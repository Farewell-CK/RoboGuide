    use super::*;
    use domain::{
        Capability, CapabilityKind, CorrelationId, EventPayload, ExecutionGroupId,
        LeaseId, NodeHealth, NodeHealthObservation, NodeHeartbeat, NodeId, NodeLease,
        NodeLiveness, NodeLivenessObservation, NodeRegistration, NodeStatus, Resource, ResourceId,
        ResourceKind, RoleAssignment, RoleId, RoleRequirement, TaskId, TaskRef, TaskRequirement,
        TimestampMs,
    };
    use ports::{EventSink, SharedNodeStateReader, SharedNodeStateWriter};
    use ports::{AllocationStateReader, AllocationStateWriter};
    use state::{InMemoryAllocationState, InMemorySharedNodeState};
    use crate::coordination::Reservation;

    /// Discards event evidence while exercising Control behavior in isolation.
    #[derive(Default)]
    struct TestEvents;

    impl EventSink for TestEvents {
        /// Ignores the event because these tests assert returned control state.
        fn append(
            &mut self,
            _timestamp: TimestampMs,
            _correlation_id: &CorrelationId,
            _causation_id: Option<&domain::EventId>,
            _payload: EventPayload,
        ) {
        }
    }

    /// Captures lifecycle evidence and correlation identities for recovery tests.
    #[derive(Default)]
    struct RecordingEvents {
        /// Event payloads paired with the correlation identity supplied by Control.
        records: Vec<(CorrelationId, EventPayload)>,
    }

    impl EventSink for RecordingEvents {
        /// Records immutable payload evidence in deterministic append order.
        fn append(
            &mut self,
            _timestamp: TimestampMs,
            correlation_id: &CorrelationId,
            _causation_id: Option<&domain::EventId>,
            payload: EventPayload,
        ) {
            self.records.push((correlation_id.clone(), payload));
        }
    }

    /// Complete in-process setup for two-role Group recovery tests.
    struct RecoveryFixture {
        /// Control instance owning reservations and Group lifecycle.
        control: ControlPlane,
        /// Cross-mission node facts read by Control through the State Port.
        state: InMemorySharedNodeState,
        /// Structured lifecycle evidence emitted by Control.
        events: RecordingEvents,
        /// Task requirements used to prove replacement availability.
        requirement: TaskRequirement,
        /// Stable Group identity retained throughout recovery.
        group_id: ExecutionGroupId,
        /// Stable task identity retained throughout recovery.
        task_ref: TaskRef,
        /// Failed role released and rebound by recovery.
        transport_role: RoleId,
        /// Unaffected role retained throughout recovery.
        compute_role: RoleId,
        /// Original transport member.
        node_a_id: NodeId,
        /// Replacement transport member, whether or not it is registered.
        node_b_id: NodeId,
        /// Unaffected compute member retained throughout recovery.
        edge_c_id: NodeId,
        /// Original transport resource released by partial recovery.
        space_a: ResourceId,
        /// Replacement transport resource committed by rebind.
        space_b: ResourceId,
        /// Additional Node B resource used to prove atomic multi-resource commit.
        space_b_secondary: ResourceId,
        /// Unaffected compute resource retained throughout recovery.
        compute_c: ResourceId,
        /// Correlation identity expected on all recovery evidence.
        correlation_id: CorrelationId,
    }

    /// Builds a single-capability registration for deterministic control tests.
    fn registration(
        node_id: &str,
        capability: CapabilityKind,
        resource_id: &str,
    ) -> NodeRegistration {
        registration_with_resource_kind(node_id, capability, resource_id, ResourceKind::Space)
    }

    /// Builds a single-capability registration with an explicit resource kind.
    fn registration_with_resource_kind(
        node_id: &str,
        capability: CapabilityKind,
        resource_id: &str,
        resource_kind: ResourceKind,
    ) -> NodeRegistration {
        NodeRegistration::new(
            NodeId::new(node_id).expect("test node id must be valid"),
            domain::LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime must be valid"),
            vec![Capability::new(capability, true)],
            vec![
                Resource::new(
                    ResourceId::new(resource_id).expect("test resource id must be valid"),
                    resource_kind,
                    1,
                )
                .expect("test resource must be valid"),
            ],
        )
    }

    /// Creates one active two-role Group with an optional transport replacement.
    fn recovery_fixture(include_replacement: bool) -> RecoveryFixture {
        let node_a = registration_with_resource_kind(
            "node-a",
            CapabilityKind::Transport,
            "space-a",
            ResourceKind::Space,
        );
        let space_b = ResourceId::new("space-b").expect("test resource id must be valid");
        let space_b_secondary =
            ResourceId::new("space-b-secondary").expect("test resource id must be valid");
        let node_b = NodeRegistration::new(
            NodeId::new("node-b").expect("test node id must be valid"),
            domain::LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime must be valid"),
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(space_b.clone(), ResourceKind::Space, 1)
                    .expect("test resource must be valid"),
                Resource::new(space_b_secondary.clone(), ResourceKind::Space, 1)
                    .expect("test resource must be valid"),
            ],
        );
        let edge_c = registration_with_resource_kind(
            "edge-c",
            CapabilityKind::Compute,
            "compute-c",
            ResourceKind::Compute,
        );
        let node_a_id = node_a.node_id().clone();
        let node_b_id = node_b.node_id().clone();
        let edge_c_id = edge_c.node_id().clone();
        let space_a = ResourceId::new("space-a").expect("test resource id must be valid");
        let compute_c = ResourceId::new("compute-c").expect("test resource id must be valid");
        let transport_role = RoleId::new("transport").expect("test role id must be valid");
        let compute_role = RoleId::new("compute").expect("test role id must be valid");
        let requirement = TaskRequirement::new(
            domain::MissionId::new("mission-recovery").expect("test mission id must be valid"),
            TaskId::new("task-01").expect("test task id must be valid"),
            vec![
                RoleRequirement::new(
                    transport_role.clone(),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
                RoleRequirement::new(
                    compute_role.clone(),
                    CapabilityKind::Compute,
                    Some(ResourceKind::Compute),
                ),
            ],
        )
        .expect("test requirement must be valid");
        let task_ref = requirement.task_ref().clone();
        let group_id =
            ExecutionGroupId::new("group-recovery").expect("test group id must be valid");
        let correlation_id =
            CorrelationId::new("recovery-trace").expect("test correlation id must be valid");
        let timestamp = TimestampMs::new(0);
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = RecordingEvents::default();
        let mut registrations = vec![node_a, edge_c];
        if include_replacement {
            registrations.push(node_b);
        }
        for node in registrations {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation_id,
                    &mut events,
                )
                .expect("test node registration should succeed");
        }
        let candidates = control
            .match_capabilities(
                &state,
                &requirement,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("initial role matching should succeed");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                vec![
                    RoleAssignment::new(
                        transport_role.clone(),
                        node_a_id.clone(),
                        vec![space_a.clone()],
                    ),
                    RoleAssignment::new(
                        compute_role.clone(),
                        edge_c_id.clone(),
                        vec![compute_c.clone()],
                    ),
                ],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("initial proposal should succeed");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("initial proposal should commit");
        control
            .create_group(
                group_id.clone(),
                &plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test Group should bind");
        control
            .activate_group(&group_id, timestamp, &correlation_id, &mut events)
            .expect("test Group should activate");
        RecoveryFixture {
            control,
            state,
            events,
            requirement,
            group_id,
            task_ref,
            transport_role,
            compute_role,
            node_a_id,
            node_b_id,
            edge_c_id,
            space_a,
            space_b,
            space_b_secondary,
            compute_c,
            correlation_id,
        }
    }

    /// Builds a one-role task requirement for a control test.
    fn requirement(task_id: &str, role_id: &str, capability: CapabilityKind) -> TaskRequirement {
        requirement_for_mission("mission-control-test", task_id, role_id, capability)
    }

    /// Builds a one-role task requirement in an explicit mission namespace.
    fn requirement_for_mission(
        mission_id: &str,
        task_id: &str,
        role_id: &str,
        capability: CapabilityKind,
    ) -> TaskRequirement {
        TaskRequirement::new(
            domain::MissionId::new(mission_id).expect("test mission id must be valid"),
            TaskId::new(task_id).expect("test task id must be valid"),
            vec![RoleRequirement::new(
                RoleId::new(role_id).expect("test role id must be valid"),
                capability,
                Some(ResourceKind::Space),
            )],
        )
        .expect("test requirement must be valid")
    }

    /// Creates the correlation identity shared by one deterministic test.
    fn correlation() -> CorrelationId {
        CorrelationId::new("control-test-trace").expect("test correlation id must be valid")
    }

    /// Moves a recovery fixture from Active to Blocked without releasing bindings.
    fn block_fixture(fixture: &mut RecoveryFixture) {
        fixture
            .control
            .block_group(
                &fixture.group_id,
                "transport role cannot progress",
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("active fixture should become blocked");
    }

    /// Marks the fixture's assigned transport node unreachable in Shared State.
    fn mark_transport_unreachable(fixture: &mut RecoveryFixture, timestamp: TimestampMs) {
        fixture
            .state
            .record_node_liveness(
                &fixture.node_a_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, timestamp),
            )
            .expect("transport liveness observation should be accepted");
    }

    /// Assesses the fixture and returns its single transport recovery need.
    fn assess_transport_recovery(
        fixture: &mut RecoveryFixture,
        timestamp: TimestampMs,
    ) -> RoleRecoveryNeed {
        match fixture
            .control
            .assess_group(
                &fixture.state,
                &fixture.group_id,
                &fixture.requirement,
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("fixture assessment should succeed")
        {
            ReconciliationAssessment::RoleRecoveryRequired(need) => need,
            ReconciliationAssessment::NoAction => {
                panic!("unreachable transport assignment should require recovery")
            }
        }
    }

    /// Detects transport unavailability and leaves the fixture Blocked and unbound.
    fn begin_detected_transport_recovery(fixture: &mut RecoveryFixture) -> RoleRecoveryNeed {
        mark_transport_unreachable(fixture, TimestampMs::new(1));
        let need = assess_transport_recovery(fixture, TimestampMs::new(1));
        fixture
            .control
            .begin_role_recovery(
                &need,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("detected transport recovery should begin");
        need
    }

    /// Matches the fixture's unbound transport role without selecting a candidate.
    fn match_fixture_recovery_candidates(
        fixture: &mut RecoveryFixture,
        need: &RoleRecoveryNeed,
        timestamp: TimestampMs,
    ) -> RecoveryCandidateSet {
        fixture
            .control
            .match_recovery_candidates(
                &fixture.state,
                need,
                &fixture.requirement,
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("role-scoped recovery matching should succeed")
    }

    /// Produces a validated non-committed proposal for the fixture's known Node B.
    fn propose_fixture_node_b(
        fixture: &mut RecoveryFixture,
        candidates: &RecoveryCandidateSet,
        timestamp: TimestampMs,
    ) -> RecoveryAssignmentProposal {
        fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                candidates,
                &fixture.requirement,
                fixture.node_b_id.clone(),
                vec![fixture.space_b.clone()],
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("known Node B recovery proposal should validate")
    }

    /// Commits the fixture's proposed Node B resources without rebinding the Group.
    fn commit_fixture_node_b(
        fixture: &mut RecoveryFixture,
        proposal: &RecoveryAssignmentProposal,
        timestamp: TimestampMs,
    ) -> CommittedRecoveryAssignment {
        fixture
            .control
            .commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                proposal,
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("known Node B recovery proposal should commit")
    }

    /// Registers one additional transport replacement for recovery retry tests.
    fn register_transport_replacement(
        fixture: &mut RecoveryFixture,
        node_name: &str,
        resource_name: &str,
        timestamp: TimestampMs,
    ) -> (NodeId, ResourceId) {
        let registration = registration(node_name, CapabilityKind::Transport, resource_name);
        let node_id = registration.node_id().clone();
        let resource_id = ResourceId::new(resource_name).expect("test resource id must be valid");
        fixture
            .control
            .register_node(
                &mut fixture.state,
                registration,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("additional transport replacement should register");
        (node_id, resource_id)
    }
