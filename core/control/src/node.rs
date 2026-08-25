//! Node admission, heartbeat/lease handling, and shared eligibility policy.

use crate::{ControlError, ControlPlane, DEFAULT_NODE_LEASE_TTL_MS, is_fresh_at};
use domain::{
    CorrelationId, EventPayload, LeaseId, NodeHealthObservation, NodeHeartbeat, NodeId, NodeLease,
    NodeLiveness, NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, NodeStatus,
    RoleRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader, SharedNodeStateWriter};

impl ControlPlane {
    /// Registers one node with a generated lease and records its visibility.
    pub fn register_node<S: SharedNodeStateReader + SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
        registration: NodeRegistration,
        status: NodeStatus,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let lease_id = LeaseId::new(format!("lease-{}", registration.node_id()))
            .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        let lease = NodeLease::new(
            lease_id,
            registration.node_id().clone(),
            timestamp,
            DEFAULT_NODE_LEASE_TTL_MS,
        )
        .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        self.register_node_with_lease(
            state,
            registration,
            status,
            lease,
            timestamp,
            correlation_id,
            events,
        )
    }

    /// Registers one node with an explicit lease from the Node Contract.
    #[allow(clippy::too_many_arguments)]
    pub fn register_node_with_lease<
        S: SharedNodeStateReader + SharedNodeStateWriter,
        E: EventSink,
    >(
        &mut self,
        state: &mut S,
        registration: NodeRegistration,
        status: NodeStatus,
        lease: NodeLease,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        if lease.node_id() != registration.node_id() {
            return Err(ControlError::InvalidLease(
                "lease node does not match registration node".to_string(),
            ));
        }
        if !lease.is_active_at(timestamp) {
            return Err(ControlError::LeaseExpired {
                node_id: registration.node_id().clone(),
                lease_id: lease.lease_id().clone(),
            });
        }
        self.ensure_resource_identities_available(state, &registration)?;
        if let Some(current) = state.node(registration.node_id()) {
            self.ensure_committed_resources_remain_declared(current, &registration)?;
        }
        let node_id = registration.node_id().clone();
        let lease_id = lease.lease_id().clone();
        state
            .record_node(NodeStateSnapshot::new(
                registration,
                status,
                timestamp,
                NodeLivenessObservation::new(NodeLiveness::Reachable, timestamp),
            ))
            .map_err(ControlError::SharedState)?;
        self.leases.insert(node_id.clone(), lease);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::NodeRegistered { node_id, lease_id },
        );
        Ok(())
    }

    /// Replaces declarations for a current node while preserving health, liveness, and lease time.
    pub fn update_node_registration<
        S: SharedNodeStateReader + SharedNodeStateWriter,
        E: EventSink,
    >(
        &mut self,
        state: &mut S,
        registration: NodeRegistration,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let node_id = registration.node_id().clone();
        let current = state
            .node(&node_id)
            .cloned()
            .ok_or_else(|| ControlError::UnknownNode(node_id.clone()))?;
        let lease = self
            .leases
            .get(&node_id)
            .ok_or_else(|| ControlError::UnknownNode(node_id.clone()))?;
        if !lease.is_active_at(timestamp) {
            return Err(ControlError::LeaseExpired {
                node_id,
                lease_id: lease.lease_id().clone(),
            });
        }
        self.ensure_resource_identities_available(state, &registration)?;
        self.ensure_committed_resources_remain_declared(&current, &registration)?;
        let lease_id = lease.lease_id().clone();
        state
            .record_node(NodeStateSnapshot::new(
                registration,
                current.reported_status(),
                current.reported_status_received_at(),
                current.liveness(),
            ))
            .map_err(ControlError::SharedState)?;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::NodeRegistered { node_id, lease_id },
        );
        Ok(())
    }

    /// Accepts a heartbeat, refreshes its health snapshot, and renews its lease.
    pub fn accept_heartbeat<S: SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
        heartbeat: NodeHeartbeat,
        received_at: TimestampMs,
        lease_duration_ms: u64,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let lease = self
            .leases
            .get_mut(heartbeat.node_id())
            .ok_or_else(|| ControlError::UnknownNode(heartbeat.node_id().clone()))?;
        if lease.lease_id() != heartbeat.lease_id() {
            return Err(ControlError::UnknownLease {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            });
        }
        let renewed_lease =
            lease
                .renew(received_at, lease_duration_ms)
                .map_err(|error| match error {
                    domain::DomainError::LeaseExpired { .. } => ControlError::LeaseExpired {
                        node_id: heartbeat.node_id().clone(),
                        lease_id: heartbeat.lease_id().clone(),
                    },
                    other => ControlError::InvalidLease(other.to_string()),
                })?;
        state
            .record_node_health(NodeHealthObservation::new(
                heartbeat.node_id().clone(),
                heartbeat.status(),
                received_at,
            ))
            .map_err(ControlError::SharedState)?;
        *lease = renewed_lease;
        events.append(
            received_at,
            correlation_id,
            None,
            EventPayload::NodeHeartbeatAccepted {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            },
        );
        Ok(())
    }

    /// Expires leases and records affected nodes as unreachable without changing reported health.
    pub fn expire_leases<S: SharedNodeStateReader + SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
        now: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<Vec<NodeId>, ControlError> {
        let expired = self
            .leases
            .iter()
            .filter(|(node_id, lease)| {
                !lease.is_active_at(now)
                    && state.node(node_id).is_some_and(|snapshot| {
                        snapshot.liveness().liveness() == NodeLiveness::Reachable
                    })
            })
            .map(|(node_id, lease)| (node_id.clone(), lease.lease_id().clone()))
            .collect::<Vec<_>>();
        for (node_id, lease_id) in &expired {
            state
                .record_node_liveness(
                    node_id,
                    NodeLivenessObservation::new(NodeLiveness::Unreachable, now),
                )
                .map_err(ControlError::SharedState)?;
            events.append(
                now,
                correlation_id,
                None,
                EventPayload::NodeLeaseExpired {
                    node_id: node_id.clone(),
                    lease_id: lease_id.clone(),
                },
            );
        }
        Ok(expired.into_iter().map(|(node_id, _)| node_id).collect())
    }

    /// Returns whether one node currently satisfies Control execution eligibility for a role.
    pub(crate) fn node_is_eligible_for_role<S: SharedNodeStateReader>(
        &self,
        state: &S,
        node_id: &NodeId,
        role: &RoleRequirement,
        timestamp: TimestampMs,
    ) -> bool {
        state.node(node_id).is_some_and(|snapshot| {
            snapshot.reported_status().health().is_schedulable()
                && is_fresh_at(
                    snapshot.reported_status_received_at(),
                    timestamp,
                    self.max_status_age_ms,
                )
                && snapshot.liveness().liveness() == NodeLiveness::Reachable
                && self
                    .leases
                    .get(node_id)
                    .is_some_and(|lease| lease.is_active_at(timestamp))
                && snapshot.registration().supports_role(role)
        })
    }

    /// Rejects resource identities already advertised by a different node.
    fn ensure_resource_identities_available<S: SharedNodeStateReader>(
        &self,
        state: &S,
        registration: &NodeRegistration,
    ) -> Result<(), ControlError> {
        for snapshot in state.nodes() {
            if snapshot.node_id() == registration.node_id() {
                continue;
            }
            for resource in registration.resources() {
                if snapshot.registration().owns_resource(resource.id(), None) {
                    return Err(ControlError::InvalidProposal(format!(
                        "resource identity {} is already advertised by node {}, not node {}",
                        resource.id(),
                        snapshot.node_id(),
                        registration.node_id()
                    )));
                }
            }
        }
        Ok(())
    }

    /// Prevents a registration update from invalidating an authoritative reservation.
    fn ensure_committed_resources_remain_declared(
        &self,
        current: &NodeStateSnapshot,
        replacement: &NodeRegistration,
    ) -> Result<(), ControlError> {
        for resource in current.registration().resources() {
            if self.reservations.contains_key(resource.id())
                && !replacement.owns_resource(resource.id(), Some(resource.kind()))
            {
                return Err(ControlError::InvalidProposal(format!(
                    "registration update changes committed resource {}",
                    resource.id()
                )));
            }
        }
        Ok(())
    }
}
