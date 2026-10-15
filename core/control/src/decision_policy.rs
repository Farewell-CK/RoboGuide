//! Control-owned explicit State consumer gates and durable policy administration.

use crate::{ControlError, ControlPlane};
use domain::{
    DecisionEvidenceReceipt, DecisionKind, DecisionPolicyRegistry, EvidenceDisposition, NodeId,
    OperationRef, RoleRequirement, TimestampMs,
};
use ports::SharedNodeStateReader;

impl ControlPlane {
    /// Rechecks an exact retained command's current consumer evidence without granting ownership.
    pub fn bound_command_decision_allowed<S: SharedNodeStateReader>(
        &self,
        state: &S,
        command: &domain::ExecutionCommand,
        decision: DecisionKind,
        now: TimestampMs,
    ) -> bool {
        let Some(group) = self.group(command.group_id()) else {
            return false;
        };
        let Some(role) = group.role_requirement(command.task_ref(), command.role_id()) else {
            return false;
        };
        let owner_matches = group
            .task_execution(command.task_ref())
            .is_some_and(|task| {
                task.assignments().iter().any(|assignment| {
                    assignment.role_id() == command.role_id()
                        && assignment.node_id() == command.node_id()
                })
            });
        owner_matches
            && self.node_tenant_allowed(command.mission_id(), command.node_id())
            && self.node_is_eligible_for_role_operation(
                state,
                command.node_id(),
                role,
                command.intent().operation(),
                now,
            )
            && self.decision_gates_allow(
                state,
                command.node_id(),
                role,
                Some(command.intent().operation()),
                decision,
                now,
            )
    }

    /// Audits one exact current logical decision, retaining the selected raw source and original time.
    #[allow(clippy::too_many_arguments)]
    pub fn audit_role_decision<S: SharedNodeStateReader, E: ports::EventSink>(
        &self,
        state: &S,
        task: &domain::TaskRef,
        node: &NodeId,
        role: &RoleRequirement,
        operation: Option<&OperationRef>,
        decision: DecisionKind,
        now: TimestampMs,
        correlation: &domain::CorrelationId,
        events: &mut E,
    ) -> bool {
        let receipts = self.evaluate_decision_gates(state, node, role, operation, decision, now);
        let allowed = receipts.iter().all(|receipt| receipt.condition_passed);
        if !receipts.is_empty() {
            events.append(
                now,
                correlation,
                None,
                domain::EventPayload::StatePolicyDecision {
                    task_ref: task.clone(),
                    receipts,
                },
            );
        }
        allowed
    }
    /// Returns an original command result only for the same route, content and authenticated principal.
    pub fn registry_command_receipt(
        &self,
        command_id: &str,
        kind: domain::RegistryCommandKind,
        input_digest: &str,
        subject: &str,
    ) -> Result<Option<domain::RegistryCommandReceipt>, ControlError> {
        if command_id.trim().is_empty()
            || command_id.len() > 256
            || subject.trim().is_empty()
            || subject.len() > 256
        {
            return Err(ControlError::InvalidProposal(
                "registry command identity is invalid".into(),
            ));
        }
        self.registry_command_receipts
            .get(command_id)
            .map_or(Ok(None), |receipt| {
                if receipt.kind == kind
                    && receipt.input_digest == input_digest
                    && receipt.authority_subject == subject
                {
                    Ok(Some(receipt.clone()))
                } else {
                    Err(ControlError::InvalidProposal(
                        "registry command identity conflicts".into(),
                    ))
                }
            })
    }

    /// Retains one original administration result in the same checkpoint as the registry update.
    pub fn retain_registry_command_receipt(
        &mut self,
        receipt: domain::RegistryCommandReceipt,
    ) -> Result<(), ControlError> {
        if self.registry_command_receipts.len() >= 2048
            || self
                .registry_command_receipts
                .contains_key(&receipt.command_id)
        {
            return Err(ControlError::InvalidProposal(
                "registry command history is full/conflicting".into(),
            ));
        }
        self.validate_registry_command_receipt(&receipt)?;
        self.registry_command_receipts
            .insert(receipt.command_id.clone(), receipt);
        Ok(())
    }

    /// Checks retained receipt attribution and its exact historical registry content identity.
    fn validate_registry_command_receipt(
        &self,
        receipt: &domain::RegistryCommandReceipt,
    ) -> Result<(), ControlError> {
        use sha2::{Digest, Sha256};
        let value = match receipt.kind {
            domain::RegistryCommandKind::BeliefPolicy => self
                .belief_policy_history
                .get(&receipt.revision)
                .map(serde_json::to_value),
            domain::RegistryCommandKind::StatePolicy => self
                .decision_policy_history
                .get(&receipt.revision)
                .map(serde_json::to_value),
            domain::RegistryCommandKind::SemanticVerifier => self
                .semantic_verifier_history
                .get(&receipt.revision)
                .map(serde_json::to_value),
        }
        .transpose()
        .map_err(|error| ControlError::InvalidProposal(error.to_string()))?
        .ok_or_else(|| {
            ControlError::InvalidProposal("registry command lacks original history".into())
        })?;
        let digest = format!(
            "sha256:{:x}",
            Sha256::digest(
                serde_json::to_vec(&value)
                    .map_err(|error| ControlError::InvalidProposal(error.to_string()))?
            )
        );
        if receipt.command_id.trim().is_empty()
            || receipt.command_id.len() > 256
            || receipt.authority_subject.trim().is_empty()
            || receipt.authority_subject.len() > 256
            || receipt.registry_digest != digest
            || !valid_registry_digest(&receipt.input_digest)
        {
            return Err(ControlError::InvalidProposal(
                "registry command receipt is invalid".into(),
            ));
        }
        Ok(())
    }
    /// Records the exact admitted source interpretation inside the caller's application transaction.
    pub(crate) fn audit_committed_decision<S: SharedNodeStateReader, E: ports::EventSink>(
        &self,
        state: &S,
        proposal: &crate::AssignmentProposal,
        now: TimestampMs,
        correlation: &domain::CorrelationId,
        events: &mut E,
    ) {
        let receipts = proposal
            .assignments()
            .iter()
            .flat_map(|assignment| {
                let role = proposal
                    .requirement_for_role(assignment.role_id())
                    .expect("validated proposal role");
                let operation = proposal.operation_for_role(assignment.role_id());
                [DecisionKind::Match, DecisionKind::Commit]
                    .into_iter()
                    .flat_map(move |decision| {
                        self.evaluate_decision_gates(
                            state,
                            assignment.node_id(),
                            role,
                            operation,
                            decision,
                            now,
                        )
                    })
            })
            .collect::<Vec<_>>();
        if !receipts.is_empty() {
            events.append(
                now,
                correlation,
                None,
                domain::EventPayload::StatePolicyDecision {
                    task_ref: proposal.task_ref().clone(),
                    receipts,
                },
            );
        }
    }
    /// Returns the current durable source and consumer admission contracts.
    pub fn decision_policy_registry(&self) -> Option<&DecisionPolicyRegistry> {
        self.decision_policy_registry.as_ref()
    }

    /// Admits a complete expected-revision registry update; State bytes grant no policy authority.
    pub fn accept_decision_policy_registry(
        &mut self,
        registry: DecisionPolicyRegistry,
        expected_revision: u64,
    ) -> Result<(), ControlError> {
        registry.validate().map_err(ControlError::InvalidProposal)?;
        match &self.decision_policy_registry {
            Some(previous) => {
                if previous == &registry && previous.revision == expected_revision {
                    return Ok(());
                }
                if previous.revision != expected_revision {
                    return Err(ControlError::InvalidProposal(
                        "decision registry expected revision conflicts".into(),
                    ));
                }
                previous
                    .validates_successor(&registry)
                    .map_err(ControlError::InvalidProposal)?;
            }
            None if expected_revision == 0 && registry.revision == 1 => {}
            None => {
                return Err(ControlError::InvalidProposal(
                    "initial decision registry requires revision one".into(),
                ));
            }
        }
        if self.decision_policy_history.len() >= 1024 {
            return Err(ControlError::InvalidProposal(
                "retained policy history is full".into(),
            ));
        }
        self.validate_decision_registry_scope(&registry)?;
        self.decision_policy_history
            .insert(registry.revision, registry.clone());
        self.decision_policy_registry = Some(registry);
        Ok(())
    }

    /// Rejects cross-tenant Node sources and unqualified named World/Belief producers.
    fn validate_decision_registry_scope(
        &self,
        registry: &DecisionPolicyRegistry,
    ) -> Result<(), ControlError> {
        for gate in registry.gates.values() {
            for source in &gate.policy.allowed_sources {
                let allowed = match source {
                    domain::StateSource::Node { node_id, .. } => self
                        .node_identity_registry()
                        .map_or(gate.tenant_id == "local", |identities| {
                            identities
                                .records
                                .get(node_id.as_str())
                                .is_some_and(|identity| identity.tenant_id == gate.tenant_id)
                        }),
                    domain::StateSource::RoboGuide { component } => {
                        component.starts_with(&format!("belief/{}/", gate.tenant_id))
                            || component.starts_with(&format!("verifier/{}/", gate.tenant_id))
                    }
                };
                if !allowed {
                    return Err(ControlError::InvalidProposal(
                        "decision source is outside its tenant/producer authority".into(),
                    ));
                }
            }
        }
        Ok(())
    }

    /// Rechecks history, schema and tenant attribution without renewing any State evidence on restore.
    pub(crate) fn validate_decision_policy_checkpoint(&self) -> Result<(), ControlError> {
        if self.decision_policy_history.len() > 1024 || self.registry_command_receipts.len() > 2048
        {
            return Err(ControlError::InvalidProposal(
                "policy history is invalid".into(),
            ));
        }
        let mut previous: Option<&DecisionPolicyRegistry> = None;
        for (revision, registry) in &self.decision_policy_history {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if revision != &registry.revision {
                return Err(ControlError::InvalidProposal(
                    "policy history revision conflicts".into(),
                ));
            }
            if let Some(old) = previous {
                old.validates_successor(registry)
                    .map_err(ControlError::InvalidProposal)?;
            } else if *revision != 1 {
                return Err(ControlError::InvalidProposal(
                    "policy history does not start at one".into(),
                ));
            }
            previous = Some(registry);
        }
        if let Some(registry) = &self.decision_policy_registry {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            self.validate_decision_registry_scope(registry)?;
            if previous != Some(registry) {
                return Err(ControlError::InvalidProposal(
                    "policy registry lacks its original history".into(),
                ));
            }
        } else if !self.decision_policy_history.is_empty() {
            return Err(ControlError::InvalidProposal(
                "policy history has no current authority".into(),
            ));
        }
        for (command, receipt) in &self.registry_command_receipts {
            if command != &receipt.command_id {
                return Err(ControlError::InvalidProposal(
                    "registry command key conflicts".into(),
                ));
            }
            self.validate_registry_command_receipt(receipt)?;
        }
        Ok(())
    }

    /// Evaluates every applicable source contract over the supplied immutable State federation.
    #[allow(clippy::too_many_arguments)]
    pub fn evaluate_decision_gates<S: SharedNodeStateReader>(
        &self,
        state: &S,
        node: &NodeId,
        role: &RoleRequirement,
        operation: Option<&OperationRef>,
        decision: DecisionKind,
        now: TimestampMs,
    ) -> Vec<DecisionEvidenceReceipt> {
        let Some(registry) = &self.decision_policy_registry else {
            return Vec::new();
        };
        let tenant = self
            .node_identity_registry()
            .and_then(|identities| identities.records.get(node.as_str()))
            .map_or("local", |identity| identity.tenant_id.as_str());
        let digest = registry.digest().expect("validated registry serializes");
        registry
            .gates
            .values()
            .filter(|gate| {
                gate.enabled
                    && gate.tenant_id == tenant
                    && gate.policy.allowed_decisions.contains(&decision)
                    && gate
                        .target
                        .role
                        .as_ref()
                        .is_none_or(|expected| expected == role.role_id())
                    && gate
                        .target
                        .capability
                        .is_none_or(|kind| role.capability() == Some(kind))
                    && gate
                        .target
                        .capability_contract
                        .as_ref()
                        .is_none_or(|contract| {
                            role.capability_requirements()
                                .iter()
                                .any(|required| required.contract() == contract)
                        })
                    && gate
                        .target
                        .operation
                        .as_ref()
                        .is_none_or(|expected| operation == Some(expected))
            })
            .map(|gate| {
                let records = state
                    .decision_records()
                    .into_iter()
                    .filter(|record| {
                        let source = record.key().source().node_id();
                        self.named_producer_record_is_current(record, now)
                            && (!gate.candidate_source_only || source == Some(node))
                            && (!gate.require_current_session
                                || source.is_none_or(|source| {
                                    record.source_epoch().is_some_and(|epoch| {
                                        self.source_session_is_current(source, epoch, now)
                                    })
                                }))
                    })
                    .collect::<Vec<_>>();
                let evaluation = gate.policy.evaluate(&records, decision, now);
                let condition_passed = evaluation.disposition == EvidenceDisposition::Accepted
                    && evaluation
                        .record
                        .as_ref()
                        .is_some_and(|record| gate.condition.matches(record));
                DecisionEvidenceReceipt {
                    registry_digest: digest.clone(),
                    policy_id: gate.policy.policy_id.clone(),
                    policy_revision: gate.policy.revision,
                    decision,
                    node_id: node.clone(),
                    disposition: evaluation.disposition,
                    condition_passed,
                    record: evaluation.record,
                    decided_at: now,
                }
            })
            .collect()
    }

    /// Requires all configured conditions; a missing projection cannot act as affirmative evidence.
    pub(crate) fn decision_gates_allow<S: SharedNodeStateReader>(
        &self,
        state: &S,
        node: &NodeId,
        role: &RoleRequirement,
        operation: Option<&OperationRef>,
        decision: DecisionKind,
        now: TimestampMs,
    ) -> bool {
        self.evaluate_decision_gates(state, node, role, operation, decision, now)
            .iter()
            .all(|receipt| receipt.condition_passed)
    }
}

/// Checks canonical opaque content identity without reading payload bytes.
fn valid_registry_digest(digest: &str) -> bool {
    digest.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}
