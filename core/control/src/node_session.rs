//! Continuous current-session evidence admitted only by Control, independent of reported health.

use crate::{ControlError, ControlPlane};
use domain::{
    CorrelationId, EventPayload, LeaseId, NodeHealthObservation, NodeHeartbeat, NodeId, NodeLease,
    NodeLiveness, NodeSessionLivenessEvidence, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader, SharedNodeStateWriter};

/// Process-local registration anchor; restore clears it with live lease authority.
#[derive(Debug, Clone)]
pub(crate) struct NodeSessionAnchor {
    /// Actual X.509 expiry cannot be renewed with the transport lease.
    pub(super) certificate_expires_at_ms: Option<u64>,
    /// Exact TLS leaf accepted with registration; absent only for explicit legacy profiles.
    pub(super) certificate_digest: Option<String>,
    /// Exact boot-unique session admitted by application registration.
    session_id: String,
    /// Exact lease admitted alongside the session.
    lease_id: LeaseId,
    /// Initial Control lease issue time, never refreshed by subsequent evidence.
    admitted_at: TimestampMs,
    /// Immutable renewal duration from startup configuration.
    duration_ms: u64,
    /// Highest admitted continuous heartbeat sequence.
    accepted_sequence: u64,
    /// Last continuous receive timestamp admitted by Control.
    accepted_received_at: TimestampMs,
    /// Durable expiry/route-loss already occurred; only registration clears the fence.
    fenced: bool,
}

impl ControlPlane {
    /// Returns the exact current session anchor for an application-side receipt fence.
    pub fn current_node_session(&self, node_id: &NodeId) -> Option<&str> {
        self.node_sessions
            .get(node_id)
            .filter(|session| !session.fenced)
            .map(|session| session.session_id.as_str())
    }
    /// Checks a source epoch against current registration and unexpired lease without refreshing either.
    pub fn source_session_is_current(
        &self,
        node_id: &NodeId,
        session_id: &str,
        now: TimestampMs,
    ) -> bool {
        self.identity_transport_is_current(node_id, now)
            && self.node_sessions.get(node_id).is_some_and(|session| {
                !session.fenced
                    && session.session_id == session_id
                    && self.leases.get(node_id).is_some_and(|lease| {
                        lease.lease_id() == &session.lease_id
                            && now >= lease.issued_at()
                            && now < lease.expires_at()
                    })
            })
    }
    /// Associates application-accepted registration with exact process-local session and lease authority.
    pub fn register_node_session(
        &mut self,
        node_id: &NodeId,
        session_id: &str,
        duration_ms: u64,
    ) -> Result<(), ControlError> {
        self.register_authenticated_node_session(node_id, session_id, duration_ms, None, None)
    }

    /// Binds an authenticated registration to its original leaf and the current lease.
    pub fn register_authenticated_node_session(
        &mut self,
        node_id: &NodeId,
        session_id: &str,
        duration_ms: u64,
        certificate_digest: Option<String>,
        certificate_expires_at_ms: Option<u64>,
    ) -> Result<(), ControlError> {
        let lease = self
            .leases
            .get(node_id)
            .ok_or_else(|| ControlError::UnknownNode(node_id.clone()))?;
        if session_id.trim().is_empty() || duration_ms == 0 {
            return Err(ControlError::InvalidLease(
                "Node session and duration must be valid".into(),
            ));
        }
        if self.node_identity_registry().is_some_and(|registry| {
            certificate_digest.as_deref().is_none_or(|digest| {
                !registry.accepts(node_id.as_str(), digest, lease.issued_at().as_millis())
            })
        }) {
            return Err(ControlError::InvalidLease(
                "registration lacks current authenticated certificate authority".into(),
            ));
        }
        if certificate_expires_at_ms.is_some_and(|until| lease.issued_at().as_millis() >= until)
            || (self.node_identity_registry().is_some() && certificate_expires_at_ms.is_none())
        {
            return Err(ControlError::InvalidLease(
                "registration lacks valid original X.509 expiry".into(),
            ));
        }
        if self.node_sessions.contains_key(node_id) {
            return Err(ControlError::InvalidLease(
                "session anchor requires a fresh registration".into(),
            ));
        }
        self.node_sessions.insert(
            node_id.clone(),
            NodeSessionAnchor {
                certificate_expires_at_ms,
                certificate_digest,
                session_id: session_id.into(),
                lease_id: lease.lease_id().clone(),
                admitted_at: lease.issued_at(),
                duration_ms,
                accepted_sequence: 0,
                accepted_received_at: lease.issued_at(),
                fenced: false,
            },
        );
        Ok(())
    }

    /// Fences an existing lease after a committed expiry or exact current route-loss observation.
    pub fn fence_node_session(&mut self, node_id: &NodeId) {
        if let Some(session) = self.node_sessions.get_mut(node_id) {
            session.fenced = true;
        }
    }

    /// Admits a continuous heartbeat interval in a caller-owned durable transaction, without health mutation.
    pub fn accept_session_liveness<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        state: &S,
        evidence: &NodeSessionLivenessEvidence,
        decision_at: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<bool, ControlError> {
        let Some(session) = self.node_sessions.get(&evidence.node_id) else {
            return Ok(false);
        };
        let Some(lease) = self.leases.get(&evidence.node_id) else {
            return Ok(false);
        };
        if session.fenced
            || session.session_id != evidence.session_id
            || session.lease_id != evidence.lease_id
            || lease.lease_id() != &evidence.lease_id
            || evidence.last_sequence <= session.accepted_sequence
            || evidence.first_sequence == 0
            || evidence.last_sequence < evidence.first_sequence
            || evidence.activated_at < session.admitted_at
            || evidence.first_received_at < evidence.activated_at
            || evidence.first_received_at > evidence.last_received_at
            || evidence.last_received_at > decision_at
            || evidence.last_received_at < session.accepted_received_at
            || evidence.maximum_gap_ms >= session.duration_ms
            || evidence
                .first_received_at
                .as_millis()
                .saturating_sub(session.admitted_at.as_millis())
                >= session.duration_ms
            || state
                .node(&evidence.node_id)
                .is_none_or(|node| node.liveness().liveness() != NodeLiveness::Reachable)
        {
            return Ok(false);
        }
        let extended = NodeLease::new(
            evidence.lease_id.clone(),
            evidence.node_id.clone(),
            evidence.last_received_at,
            session.duration_ms,
        )
        .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        if !extended.is_active_at(decision_at) {
            return Ok(false);
        }
        if extended.expires_at() > lease.expires_at() {
            self.leases.insert(evidence.node_id.clone(), extended);
        }
        let session = self
            .node_sessions
            .get_mut(&evidence.node_id)
            .expect("session validated without mutation above");
        session.accepted_sequence = evidence.last_sequence;
        session.accepted_received_at = evidence.last_received_at;
        events.append(
            evidence.last_received_at,
            correlation_id,
            None,
            EventPayload::NodeSessionLivenessAccepted {
                node_id: evidence.node_id.clone(),
                session_id: evidence.session_id.clone(),
                lease_id: evidence.lease_id.clone(),
                first_sequence: evidence.first_sequence,
                last_sequence: evidence.last_sequence,
                activated_at: evidence.activated_at,
                first_received_at: evidence.first_received_at,
                last_received_at: evidence.last_received_at,
                maximum_gap_ms: evidence.maximum_gap_ms,
            },
        );
        Ok(true)
    }

    /// Separately accepts original health and maintains non-regressing liveness-only lease authority.
    #[allow(clippy::too_many_arguments)]
    pub fn accept_received_heartbeat<
        S: SharedNodeStateReader + SharedNodeStateWriter,
        E: EventSink,
    >(
        &mut self,
        state: &mut S,
        heartbeat: NodeHeartbeat,
        received_at: TimestampMs,
        decision_at: TimestampMs,
        lease_duration_ms: u64,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        if received_at > decision_at {
            return Err(ControlError::InvalidLease(
                "heartbeat receive time is after decision time".into(),
            ));
        }
        let lease = self
            .leases
            .get(heartbeat.node_id())
            .ok_or_else(|| ControlError::UnknownNode(heartbeat.node_id().clone()))?;
        if lease.lease_id() != heartbeat.lease_id() {
            return Err(ControlError::UnknownLease {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            });
        }
        if self
            .node_sessions
            .get(heartbeat.node_id())
            .is_some_and(|session| session.fenced)
            || !lease.is_active_at(decision_at)
        {
            return Err(ControlError::LeaseExpired {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            });
        }
        let renewed = if received_at >= lease.issued_at() {
            Some(
                lease
                    .renew(received_at, lease_duration_ms)
                    .map_err(|error| ControlError::InvalidLease(error.to_string()))?,
            )
        } else {
            None
        };
        state
            .record_node_health(NodeHealthObservation::new(
                heartbeat.node_id().clone(),
                heartbeat.status(),
                received_at,
            ))
            .map_err(ControlError::SharedState)?;
        if let Some(renewed) = renewed {
            self.leases.insert(heartbeat.node_id().clone(), renewed);
        }
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
}
