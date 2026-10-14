//! Control-owned durable Node identity admission and lifecycle transitions.

use crate::{ControlError, ControlPlane};
use domain::{IdentityCommand, IdentityCommandReceipt, NodeIdentityRegistry, TimestampMs};

impl ControlPlane {
    /// Returns committed administrator grants; static compatibility profiles have no watermark.
    pub fn operator_authorization(&self) -> Option<&domain::OperatorAuthorization> {
        self.operator_authorization.as_ref()
    }

    /// Admits exact deployment authority before the composition root persists and publishes it.
    pub fn accept_operator_authorization(
        &mut self,
        profile: Option<domain::OperatorAuthorization>,
    ) -> Result<(), ControlError> {
        match (&self.operator_authorization, &profile) {
            (Some(previous), Some(next)) => previous.validates_successor(next),
            (None, Some(next)) => next.validate(),
            (Some(_), None) => {
                Err("operator authorization cannot downgrade to a static profile".into())
            }
            (None, None) => Ok(()),
        }
        .map_err(ControlError::InvalidProposal)?;
        self.operator_authorization = profile;
        Ok(())
    }
    /// Returns the durable registry used by current Control eligibility and transport projection.
    pub fn node_identity_registry(&self) -> Option<&NodeIdentityRegistry> {
        self.node_identity_registry.as_ref()
    }

    /// Installs one deployment bootstrap registry; a restored authority can never be overwritten.
    pub fn bootstrap_node_identity_registry(
        &mut self,
        registry: NodeIdentityRegistry,
    ) -> Result<(), ControlError> {
        registry
            .validate()
            .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        if self.node_identity_registry.is_some() {
            return Err(ControlError::InvalidLease(
                "Node identity authority is already installed".into(),
            ));
        }
        self.node_identity_registry = Some(registry);
        Ok(())
    }

    /// Applies a revision-bound administrative change on the private application candidate.
    pub fn apply_node_identity_command(
        &mut self,
        command: &IdentityCommand,
        now: TimestampMs,
        authority_subject: &str,
    ) -> Result<IdentityCommandReceipt, ControlError> {
        self.node_identity_registry
            .as_mut()
            .ok_or_else(|| {
                ControlError::InvalidLease("dynamic Node identity profile is not enabled".into())
            })?
            .apply(command, now.as_millis(), authority_subject)
            .map_err(|error| ControlError::InvalidLease(error.to_string()))
    }

    /// Checks whether a current identity is active at the fresh Control decision time.
    pub(crate) fn identity_is_eligible(&self, node_id: &domain::NodeId, now: TimestampMs) -> bool {
        self.identity_transport_is_current(node_id, now)
            && self.node_identity_registry.as_ref().is_none_or(|registry| {
                registry
                    .records
                    .get(node_id.as_str())
                    .is_some_and(|record| {
                        record.attestation_policy.as_ref().is_none_or(|policy| {
                            !policy.required_for_admission
                                || record.attestation_report.as_ref().is_some_and(|report| {
                                    report.received_at_ms <= now.as_millis()
                                        && now.as_millis() < report.valid_until_ms
                                })
                        })
                    })
            })
    }

    /// Keeps authenticated raw observations available independently of measurement-based eligibility.
    pub(crate) fn identity_transport_is_current(
        &self,
        node_id: &domain::NodeId,
        now: TimestampMs,
    ) -> bool {
        self.node_identity_registry.as_ref().is_none_or(|registry| {
            self.node_sessions
                .get(node_id)
                .filter(|session| {
                    session
                        .certificate_expires_at_ms
                        .is_some_and(|until| now.as_millis() < until)
                })
                .and_then(|session| session.certificate_digest.as_deref())
                .is_some_and(|digest| registry.accepts(node_id.as_str(), digest, now.as_millis()))
        })
    }

    /// Installs only an application-verified challenge/report update without replacing certificate authority.
    pub fn replace_attestation_registry(
        &mut self,
        registry: NodeIdentityRegistry,
    ) -> Result<(), ControlError> {
        registry
            .validate()
            .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        let previous = self
            .node_identity_registry
            .as_ref()
            .ok_or_else(|| ControlError::InvalidLease("Node registry is absent".into()))?;
        if previous.registry_id != registry.registry_id
            || previous.retired_digests != registry.retired_digests
            || previous.commands != registry.commands
            || previous.records.len() != registry.records.len()
            || previous.records.iter().any(|(id, old)| {
                registry.records.get(id).is_none_or(|new| {
                    old.node_id != new.node_id
                        || old.tenant_id != new.tenant_id
                        || old.domain_id != new.domain_id
                        || old.current_digest != new.current_digest
                        || old.next_digest != new.next_digest
                        || old.previous_digest != new.previous_digest
                        || old.phase != new.phase
                        || old.not_before_ms != new.not_before_ms
                        || old.expires_at_ms != new.expires_at_ms
                        || old.overlap_until_ms != new.overlap_until_ms
                        || old.attestation_policy != new.attestation_policy
                        || new.revision < old.revision
                        || new.revision > old.revision.saturating_add(1)
                })
            })
        {
            return Err(ControlError::InvalidLease(
                "attestation update attempted to change certificate authority".into(),
            ));
        }
        self.node_identity_registry = Some(registry);
        Ok(())
    }

    /// Checks registration availability before its authenticated session anchor is inserted.
    pub(crate) fn identity_registration_allowed(
        &self,
        node_id: &domain::NodeId,
        now: TimestampMs,
    ) -> bool {
        self.node_identity_registry.as_ref().is_none_or(|registry| {
            registry
                .records
                .get(node_id.as_str())
                .is_some_and(|record| {
                    registry.accepts(node_id.as_str(), &record.current_digest, now.as_millis())
                })
        })
    }
}
