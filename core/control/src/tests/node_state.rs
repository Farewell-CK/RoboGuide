    /// A stale health snapshot is rejected after the configured status TTL.
    #[test]
    fn matching_rejects_stale_node_status() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let task = requirement("task-timeout", "transport", CapabilityKind::Transport);
        let observed_at = TimestampMs::new(0);
        let now = TimestampMs::new(101);
        let correlation_id = correlation();
        let mut control = ControlPlane::with_status_ttl(100);
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, observed_at),
                observed_at,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        assert!(matches!(
            control.match_capabilities(&state, &task, now, &correlation_id, &mut events),
            Err(ControlError::NoCandidate(_))
        ));
        let stored = state
            .node(&NodeId::new("node-a").expect("test node id should be valid"))
            .expect("stale facts should remain represented by State");
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(stored.reported_status().observed_at(), observed_at);
        assert_eq!(stored.reported_status_received_at(), observed_at);
    }

    /// Health freshness uses RoboGuide receive time, never source-local time.
    #[test]
    fn matching_freshness_uses_roboguide_receive_time() {
        let node = registration(
            "node-clock-domain",
            CapabilityKind::Transport,
            "space-clock",
        );
        let task = requirement("task-clock-domain", "transport", CapabilityKind::Transport);
        let correlation_id = correlation();
        let received_at = TimestampMs::new(100);
        let mut control = ControlPlane::with_status_ttl(100);
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1_000_000)),
                received_at,
                &correlation_id,
                &mut events,
            )
            .expect("registration should preserve independent source time");

        control
            .match_capabilities(
                &state,
                &task,
                TimestampMs::new(150),
                &correlation_id,
                &mut events,
            )
            .expect("receive time age 50 should be fresh despite source clock value");
        let stored = state
            .node(&NodeId::new("node-clock-domain").expect("test node id should be valid"))
            .expect("registered node should exist");
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(1_000_000)
        );
        assert_eq!(stored.reported_status_received_at(), received_at);
    }

    /// Matching observes the latest health fact instead of a Control-owned cache.
    #[test]
    fn health_update_is_visible_to_next_matching_decision() {
        let node = registration("node-health", CapabilityKind::Transport, "space-health");
        let node_id = node.node_id().clone();
        let task = requirement("task-health", "transport", CapabilityKind::Transport);
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
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
        control
            .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
            .expect("online node should initially match");

        state
            .record_node_health(NodeHealthObservation::new(
                node_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
                TimestampMs::new(1),
            ))
            .expect("newer health observation should enter Shared State");
        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// A valid heartbeat renews the lease and refreshes the node health snapshot.
    #[test]
    fn heartbeat_renews_lease_and_updates_health() {
        let node = registration(
            "node-heartbeat",
            CapabilityKind::Transport,
            "space-heartbeat",
        );
        let node_id = node.node_id().clone();
        let lease_id = LeaseId::new("lease-heartbeat").expect("test lease id must be valid");
        let lease = NodeLease::new(lease_id.clone(), node_id.clone(), TimestampMs::new(0), 100)
            .expect("test lease should be valid");
        let correlation_id = correlation();
        let mut control = ControlPlane::with_status_ttl(200);
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node_with_lease(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                lease,
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("explicit lease registration should succeed");

        control
            .accept_heartbeat(
                &mut state,
                NodeHeartbeat::new(
                    node_id.clone(),
                    lease_id,
                    NodeStatus::new(NodeHealth::Degraded, TimestampMs::new(8_000)),
                ),
                TimestampMs::new(30),
                100,
                &correlation_id,
                &mut events,
            )
            .expect("heartbeat should renew active lease");

        let stored = state
            .node(&node_id)
            .expect("heartbeat node should remain in Shared State");
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(8_000)
        );
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(30));
        assert_eq!(stored.liveness().observed_at(), TimestampMs::new(30));

        let task = requirement("task-heartbeat", "transport", CapabilityKind::Transport);
        assert!(
            control
                .match_capabilities(
                    &state,
                    &task,
                    TimestampMs::new(129),
                    &correlation_id,
                    &mut events,
                )
                .is_ok()
        );
        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                TimestampMs::new(130),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// Lease expiry changes liveness without rewriting local reported health.
    #[test]
    fn expired_lease_marks_liveness_unreachable() {
        let node = registration("node-expiring", CapabilityKind::Transport, "space-expiring");
        let node_id = node.node_id().clone();
        let task = requirement("task-expiring", "transport", CapabilityKind::Transport);
        let lease_id = LeaseId::new("lease-expiring").expect("test lease id must be valid");
        let lease = NodeLease::new(lease_id, node_id.clone(), TimestampMs::new(0), 100)
            .expect("test lease should be valid");
        let correlation_id =
            CorrelationId::new("lease-expiry-trace").expect("test correlation id must be valid");
        let mut control = ControlPlane::with_status_ttl(100);
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node_with_lease(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
                lease,
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let expired = control.expire_leases(
            &mut state,
            TimestampMs::new(100),
            &correlation_id,
            &mut events,
        );
        assert_eq!(
            expired.expect("lease expiry should update Shared State"),
            vec![node_id]
        );
        let stored = state
            .node(&NodeId::new("node-expiring").expect("test node id should be valid"))
            .expect("expired node should remain represented in State");
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(stored.reported_status().observed_at(), TimestampMs::new(10));
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(0));
        assert_eq!(
            stored.liveness(),
            NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(100))
        );
        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                TimestampMs::new(100),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// A heartbeat carrying another node's lease is rejected.
    #[test]
    fn heartbeat_rejects_unknown_lease() {
        let node = registration("node-lease-owner", CapabilityKind::Transport, "space-owner");
        let node_id = node.node_id().clone();
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let error = control
            .accept_heartbeat(
                &mut state,
                NodeHeartbeat::new(
                    node_id,
                    LeaseId::new("lease-not-owned").expect("test lease id must be valid"),
                    NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                ),
                TimestampMs::new(1),
                DEFAULT_NODE_LEASE_TTL_MS,
                &correlation_id,
                &mut events,
            )
            .expect_err("unknown lease must be rejected");
        assert!(matches!(error, ControlError::UnknownLease { .. }));
    }

    /// Resource identities are global Control keys and cannot be advertised by two nodes.
    #[test]
    fn registration_rejects_cross_node_resource_identity_conflict() {
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                registration("node-a", CapabilityKind::Transport, "shared-space"),
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first resource owner should register");

        assert!(matches!(
            control.register_node(
                &mut state,
                registration("node-b", CapabilityKind::Transport, "shared-space"),
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidProposal(reason))
                if reason.contains("shared-space")
                    && reason.contains("node-a")
                    && reason.contains("node-b")
        ));
        assert!(state
            .node(&NodeId::new("node-b").expect("test node id must be valid"))
            .is_none());
    }

    /// A group without a safe replacement is recorded as blocked, never complete.
    #[test]
    fn group_can_be_marked_blocked_after_recovery_exhaustion() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_id = node.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let group_id = ExecutionGroupId::new("group-blocked").expect("test group id must be valid");
        let task = requirement("task-blocked", "transport", CapabilityKind::Transport);
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
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
        let candidates = control
            .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
            .expect("task should initially match");
        let proposal = control
            .propose(
                &state,
                &task,
                &candidates,
                vec![RoleAssignment::new(role_id, node_id, vec![resource_id])],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("proposal should be valid");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("proposal should commit");
        control
            .create_group(
                group_id.clone(),
                &plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("group should bind before recovery exhaustion");
        control
            .activate_group(&group_id, timestamp, &correlation_id, &mut events)
            .expect("group should activate before becoming blocked");

        control
            .block_group(
                &group_id,
                "no safe replacement is available",
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            )
            .expect("blocked transition should be recorded");
        assert_eq!(
            control
                .group(&group_id)
                .expect("blocked group should remain observable")
                .lifecycle(),
            GroupLifecycle::Blocked
        );
    }
