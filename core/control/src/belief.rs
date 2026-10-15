//! Durable Belief source interpretation admission; computation remains a separate State provider.

use crate::{ControlError, ControlPlane};
use domain::{BeliefPolicyRegistry, StateRecord, StateSource};

impl ControlPlane {
    /// Admits a raw Belief source only under its current authenticated identity and session epoch.
    pub fn belief_source_is_current(&self, record: &StateRecord, now: domain::TimestampMs) -> bool {
        record.key().source().node_id().is_some_and(|node| {
            self.identity_is_eligible(node, now)
                && record
                    .source_epoch()
                    .map_or(self.node_identity_registry().is_none(), |epoch| {
                        self.source_session_is_current(node, epoch, now)
                    })
        })
    }

    /// Resolves State visibility from trusted source ownership rather than client-authored payload fields.
    pub fn state_source_visible_to_tenant(&self, source: &StateSource, tenant: &str) -> bool {
        match source {
            StateSource::Node { node_id, .. } => {
                self.node_identity_registry()
                    .map_or(tenant == "local", |registry| {
                        registry
                            .records
                            .get(node_id.as_str())
                            .is_some_and(|identity| identity.tenant_id == tenant)
                    })
            }
            StateSource::RoboGuide { component } => {
                self.belief_policy_registry
                    .as_ref()
                    .is_some_and(|registry| {
                        registry.policies.values().any(|policy| {
                            policy.tenant_id == tenant
                                && component == &format!("belief/{}/{}", tenant, policy.policy_id)
                        })
                    })
                    || self
                        .semantic_verifier_registry
                        .as_ref()
                        .is_some_and(|registry| {
                            registry.sources.values().any(|source| {
                                source.tenant_id == tenant
                                    && component
                                        == &format!("verifier/{}/{}", tenant, source.source_id)
                            })
                        })
            }
        }
    }
    /// Returns explicit current derivation contracts, without performing fusion or granting commitments.
    pub fn belief_policy_registry(&self) -> Option<&BeliefPolicyRegistry> {
        self.belief_policy_registry.as_ref()
    }

    /// Replaces one source registry only at its expected revision and within authenticated tenant ownership.
    pub fn accept_belief_policy_registry(
        &mut self,
        next: BeliefPolicyRegistry,
        expected_revision: u64,
    ) -> Result<(), ControlError> {
        next.validate().map_err(ControlError::InvalidProposal)?;
        match &self.belief_policy_registry {
            Some(previous) if previous == &next && previous.revision == expected_revision => {
                return Ok(());
            }
            Some(previous) if previous.revision == expected_revision => previous
                .validates_successor(&next)
                .map_err(ControlError::InvalidProposal)?,
            None if expected_revision == 0 && next.revision == 1 => {}
            _ => {
                return Err(ControlError::InvalidProposal(
                    "Belief registry expected revision conflicts".into(),
                ));
            }
        }
        if self.belief_policy_history.len() >= 1024 {
            return Err(ControlError::InvalidProposal(
                "Belief history is full".into(),
            ));
        }
        for policy in next.policies.values() {
            for source in &policy.admission.allowed_sources {
                let Some(node) = source.node_id() else {
                    return Err(ControlError::InvalidProposal(
                        "Belief inputs must have raw Node attribution".into(),
                    ));
                };
                let allowed =
                    self.node_identity_registry()
                        .map_or(policy.tenant_id == "local", |registry| {
                            registry
                                .records
                                .get(node.as_str())
                                .is_some_and(|identity| identity.tenant_id == policy.tenant_id)
                        });
                if !allowed {
                    return Err(ControlError::InvalidProposal(
                        "Belief source crosses tenant identity authority".into(),
                    ));
                }
            }
        }
        self.belief_policy_history
            .insert(next.revision, next.clone());
        self.belief_policy_registry = Some(next);
        Ok(())
    }

    /// Fences revoked/revised named producers so an older fresh State value cannot preserve eligibility.
    pub(crate) fn named_producer_record_is_current(
        &self,
        record: &StateRecord,
        now: domain::TimestampMs,
    ) -> bool {
        let StateSource::RoboGuide { component } = record.key().source() else {
            return true;
        };
        if component.starts_with("belief/") {
            self.belief_policy_registry
                .as_ref()
                .is_some_and(|registry| {
                    registry.policies.values().any(|policy| {
                        policy.enabled
                            && component
                                == &format!("belief/{}/{}", policy.tenant_id, policy.policy_id)
                            && record
                                .value()
                                .get("policy_revision")
                                .and_then(serde_json::Value::as_u64)
                                == Some(policy.revision)
                            && record
                                .value()
                                .get("status")
                                .and_then(serde_json::Value::as_str)
                                == Some("accepted")
                            && record
                                .value()
                                .get("inputs")
                                .and_then(serde_json::Value::as_array)
                                .is_some_and(|inputs| {
                                    !inputs.is_empty()
                                        && inputs.iter().all(|input| {
                                            let source = input
                                                .get("key")
                                                .and_then(|key| key.get("source"))
                                                .and_then(|value| {
                                                    serde_json::from_value::<StateSource>(
                                                        value.clone(),
                                                    )
                                                    .ok()
                                                });
                                            source.as_ref().is_some_and(|source| {
                                                policy.admission.allowed_sources.contains(source)
                                                    && source.node_id().is_some_and(|node| {
                                                        self.identity_is_eligible(node, now)
                                                        && input
                                                            .get("source_epoch")
                                                            .and_then(serde_json::Value::as_str)
                                                            .map_or(
                                                                self.node_identity_registry()
                                                                    .is_none(),
                                                                |epoch| {
                                                                    self.source_session_is_current(
                                                                        node, epoch, now,
                                                                    )
                                                                },
                                                            )
                                                    })
                                            })
                                        })
                                })
                    })
                })
        } else if component.starts_with("verifier/") {
            self.semantic_verifier_registry
                .as_ref()
                .is_some_and(|registry| {
                    registry.sources.values().any(|source| {
                        source.enabled
                            && component
                                == &format!("verifier/{}/{}", source.tenant_id, source.source_id)
                            && record
                                .value()
                                .get("source_revision")
                                .and_then(serde_json::Value::as_u64)
                                == Some(source.revision)
                    })
                })
        } else {
            false
        }
    }

    /// Restores only complete monotonic interpretation history, without recomputing or renewing evidence.
    pub(crate) fn validate_belief_policy_checkpoint(&self) -> Result<(), ControlError> {
        if self.belief_policy_history.len() > 1024 {
            return Err(ControlError::InvalidProposal(
                "Belief history bounds differ".into(),
            ));
        }
        let mut previous: Option<&BeliefPolicyRegistry> = None;
        for (revision, registry) in &self.belief_policy_history {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if *revision != registry.revision {
                return Err(ControlError::InvalidProposal(
                    "Belief history revision conflicts".into(),
                ));
            }
            if let Some(old) = previous {
                old.validates_successor(registry)
                    .map_err(ControlError::InvalidProposal)?;
            } else if *revision != 1 {
                return Err(ControlError::InvalidProposal(
                    "Belief history does not start at one".into(),
                ));
            }
            previous = Some(registry);
        }
        if self.belief_policy_registry.as_ref() != previous {
            return Err(ControlError::InvalidProposal(
                "Belief current authority differs from retained history".into(),
            ));
        }
        Ok(())
    }
}
