//! Original active evidence gates continuously delegate to complete stop/revoke authority.

use crate::*;
use ports::EventSink;
use sha2::{Digest as _, Sha256};

/// Complete bounded active-gate history; observations never grant execution or stopping proof.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct HazardState {
    /// One original current-attempt watch; terminal history remains independently inspectable.
    watches: BTreeMap<String, HazardWatch>,
}

impl HazardState {
    /// Exposes whether a legacy wrapper would hide actual active monitoring authority.
    pub(crate) fn has_authority(&self) -> bool {
        !self.watches.is_empty()
    }
}

/// Exact original command/gate identity, separate from current source observations.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct HazardWatch {
    /// Original physical-attempt identity.
    execution_id: String,
    /// Complete exact retained Runtime command digest.
    command_digest: String,
    /// Original attempt generation.
    generation: u64,
    /// Immutable original policy revision.
    registry_revision: u64,
    /// Complete original registry digest.
    registry_digest: String,
    /// Only originally applicable explicitly monitored Readiness policies.
    gates: BTreeSet<String>,
    /// First actual Controller admission time, never refreshed by observations or retry.
    admitted_at: domain::TimestampMs,
    /// First irreversible unsafe/unavailable/revoked observation; it is not a stop confirmation.
    trip: Option<HazardTrip>,
}

/// Bounded original trip identity; raw decision receipts remain in the ordinary EventLog.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct HazardTrip {
    /// First actual local observation time.
    observed_at: domain::TimestampMs,
    /// Exact failing policy identities, not a fabricated hazard measurement.
    failed: BTreeSet<String>,
    /// Hash of complete original receipts retained in the normal audit event stream.
    receipt_digest: String,
}

/// Hashes canonical typed evidence without allowing a source label to replace original bytes.
fn digest(value: &impl Serialize) -> Result<String, String> {
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json::to_vec(&serde_json::to_value(value).map_err(|error| error.to_string())?)
                .map_err(|error| error.to_string())?
        )
    ))
}

/// Returns only actual current nonterminal commands; superseded attempts keep history without new effects.
fn active_commands(controller: &ControllerState) -> Vec<(String, domain::ExecutionCommand)> {
    controller
        .bridge
        .attempt_history()
        .into_iter()
        .filter(|attempt| {
            !attempt.status().is_terminal()
                && controller.bridge.current_task_attempt_id(
                    attempt.command().group_id(),
                    attempt.command().task_ref(),
                    attempt.command().role_id(),
                ) == Some(attempt.execution_id())
        })
        .map(|attempt| {
            (
                attempt.execution_id().to_string(),
                attempt.command().clone(),
            )
        })
        .collect()
}

/// Evaluates exact retained active gates with current source/session/TTL/conflict checks.
fn evaluate(
    controller: &ControllerState,
    command: &domain::ExecutionCommand,
    watch: &HazardWatch,
    now: domain::TimestampMs,
) -> Result<(Vec<domain::DecisionEvidenceReceipt>, BTreeSet<String>), String> {
    let control = controller.bridge.control();
    let original = control
        .decision_policy_revision(watch.registry_revision)
        .ok_or("active hazard lost original registry")?;
    if original.digest()? != watch.registry_digest || digest(command)? != watch.command_digest {
        return Err("active hazard original command or registry digest changed".into());
    }
    let role = control
        .group(command.group_id())
        .and_then(|group| group.role_requirement(command.task_ref(), command.role_id()))
        .ok_or("active hazard lost original Role")?;
    let receipts: Vec<_> = control
        .evaluate_decision_registry_gates(
            &controller.bridge.decision_state(),
            original,
            command.node_id(),
            role,
            Some(command.intent().operation()),
            domain::DecisionKind::Readiness,
            now,
        )
        .into_iter()
        .filter(|receipt| watch.gates.contains(&receipt.policy_id))
        .collect();
    let mut failed = BTreeSet::new();
    for id in &watch.gates {
        if original.gates.get(id)
            != control
                .decision_policy_registry()
                .and_then(|registry| registry.gates.get(id))
            || receipts
                .iter()
                .find(|receipt| &receipt.policy_id == id)
                .is_none_or(|receipt| !receipt.condition_passed)
        {
            failed.insert(id.clone());
        }
    }
    if control
        .evaluate_decision_gates(
            &controller.bridge.decision_state(),
            command.node_id(),
            role,
            Some(command.intent().operation()),
            domain::DecisionKind::Readiness,
            now,
        )
        .iter()
        .any(|receipt| {
            control
                .decision_policy_registry()
                .and_then(|registry| registry.gates.get(&receipt.policy_id))
                .is_some_and(|gate| gate.active_stop_timeout_ms.is_some())
                && !watch.gates.contains(&receipt.policy_id)
        })
    {
        failed.extend(watch.gates.iter().cloned());
    }
    Ok((receipts, failed))
}

/// Installs original watches before delivery and durably stops complete cohorts on the first failed gate.
pub(crate) fn monitor(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let commands = active_commands(controller);
    for (id, command) in &commands {
        if controller.hazards.watches.contains_key(id.as_str()) {
            continue;
        }
        let control = controller.bridge.control();
        let Some(registry) = control.decision_policy_registry() else {
            continue;
        };
        let Some(role) = control
            .group(command.group_id())
            .and_then(|group| group.role_requirement(command.task_ref(), command.role_id()))
        else {
            continue;
        };
        let gates: BTreeSet<_> = control
            .evaluate_decision_gates(
                &controller.bridge.decision_state(),
                command.node_id(),
                role,
                Some(command.intent().operation()),
                domain::DecisionKind::Readiness,
                now,
            )
            .iter()
            .filter(|receipt| {
                registry
                    .gates
                    .get(&receipt.policy_id)
                    .is_some_and(|gate| gate.active_stop_timeout_ms.is_some())
            })
            .map(|receipt| receipt.policy_id.clone())
            .collect();
        if gates.is_empty() {
            continue;
        }
        if controller.hazards.watches.len() >= 8192 {
            return Err("original active hazard history capacity exhausted".into());
        }
        let watch = HazardWatch {
            execution_id: id.as_str().into(),
            command_digest: digest(command)?,
            generation: controller
                .bridge
                .current_attempt_generation(
                    command.group_id(),
                    command.task_ref(),
                    command.role_id(),
                )
                .ok_or("active hazard attempt generation absent")?,
            registry_revision: registry.revision,
            registry_digest: registry.digest()?,
            gates,
            admitted_at: now,
            trip: None,
        };
        controller.hazards.watches.insert(id.as_str().into(), watch);
    }
    for (id, command) in commands {
        let Some(watch) = controller.hazards.watches.get(id.as_str()).cloned() else {
            continue;
        };
        if watch.trip.is_some() {
            continue;
        }
        let (receipts, failed) = evaluate(controller, &command, &watch, now)?;
        if failed.is_empty() {
            continue;
        }
        let timeout = controller
            .bridge
            .control()
            .decision_policy_revision(watch.registry_revision)
            .and_then(|registry| {
                failed
                    .iter()
                    .filter_map(|id| registry.gates.get(id)?.active_stop_timeout_ms)
                    .min()
            })
            .ok_or("active hazard lost original stop bound")?;
        let receipt_digest = digest(&receipts)?;
        let trace = domain::CorrelationId::new("active-hazard-stop").expect("fixed trace");
        for receipt in receipts {
            controller.bridge.event_sink().clone().append(
                now,
                &trace,
                None,
                domain::EventPayload::StatePolicyDecision {
                    task_ref: command.task_ref().clone(),
                    receipts: vec![receipt],
                },
            );
        }
        super::memory_use::revoke_group(
            controller,
            command.group_id(),
            "original active hazard gate failed or was revoked",
            now,
        )?;
        super::safety_stop::admit(
            controller,
            command.group_id(),
            command.task_ref(),
            command.role_id(),
            now,
            timeout,
        )?;
        controller
            .hazards
            .watches
            .get_mut(id.as_str())
            .expect("original watch")
            .trip = Some(HazardTrip {
            observed_at: now,
            failed,
            receipt_digest,
        });
    }
    Ok(())
}

/// Skips all additional clock reads when this Group has no current or retained active-gate policy.
pub(crate) fn requires_delivery_check(
    controller: &ControllerState,
    group: &domain::ExecutionGroupId,
) -> bool {
    controller
        .bridge
        .control()
        .decision_policy_registry()
        .is_some_and(|registry| {
            registry
                .gates
                .values()
                .any(|gate| gate.enabled && gate.active_stop_timeout_ms.is_some())
        })
        || controller.bridge.attempt_history().iter().any(|attempt| {
            attempt.command().group_id() == group
                && controller
                    .hazards
                    .watches
                    .contains_key(attempt.execution_id())
        })
}

/// Refuses unmonitored, unsafe or revoked original gates on every attempted Execute delivery.
pub(crate) fn delivery_permitted(
    controller: &ControllerState,
    group: &domain::ExecutionGroupId,
    now: domain::TimestampMs,
) -> bool {
    active_commands(controller)
        .iter()
        .filter(|(_, command)| command.group_id() == group)
        .all(|(id, command)| {
            if let Some(watch) = controller.hazards.watches.get(id.as_str()) {
                return watch.trip.is_none()
                    && evaluate(controller, command, watch, now)
                        .is_ok_and(|(_, failed)| failed.is_empty());
            }
            let control = controller.bridge.control();
            let Some(role) = control
                .group(group)
                .and_then(|group| group.role_requirement(command.task_ref(), command.role_id()))
            else {
                return false;
            };
            !control
                .evaluate_decision_gates(
                    &controller.bridge.decision_state(),
                    command.node_id(),
                    role,
                    Some(command.intent().operation()),
                    domain::DecisionKind::Readiness,
                    now,
                )
                .iter()
                .any(|receipt| {
                    control
                        .decision_policy_registry()
                        .and_then(|registry| registry.gates.get(&receipt.policy_id))
                        .is_some_and(|gate| gate.active_stop_timeout_ms.is_some())
                })
        })
}

/// Revalidates retained original command, policy and stop joins without treating old evidence as current.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    if controller.hazards.watches.len() > 8192 {
        return Err("retained active hazard history exceeds its bound".into());
    }
    let history = controller.bridge.attempt_history();
    for (id, watch) in &controller.hazards.watches {
        let actual = history
            .iter()
            .find(|attempt| attempt.execution_id() == id)
            .ok_or("hazard lost original Runtime history")?;
        let original = controller
            .bridge
            .control()
            .decision_policy_revision(watch.registry_revision)
            .ok_or("hazard lost original policy history")?;
        if id != &watch.execution_id
            || digest(actual.command())? != watch.command_digest
            || original.digest()? != watch.registry_digest
            || watch.gates.is_empty()
            || watch.gates.len() > 128
            || watch.generation == 0
            || actual.command().execution_generation() != Some(watch.generation)
            || watch.gates.iter().any(|id| {
                original
                    .gates
                    .get(id)
                    .is_none_or(|gate| gate.active_stop_timeout_ms.is_none())
            })
            || watch.trip.as_ref().is_some_and(|trip| {
                trip.observed_at < watch.admitted_at
                    || trip.failed.is_empty()
                    || !trip.failed.is_subset(&watch.gates)
                    || !trip.receipt_digest.starts_with("sha256:")
                    || !controller.safety_stops.contains_key(id)
            })
        {
            return Err(
                "retained active hazard changed its original command, gate or stop evidence".into(),
            );
        }
    }
    Ok(())
}

/// Projects original Mission-local watches; no read renews source freshness or creates a certificate.
pub(crate) fn view(controller: &ControllerState, mission: &domain::MissionId) -> serde_json::Value {
    let ids: BTreeSet<_> = controller
        .bridge
        .attempt_history()
        .iter()
        .filter(|attempt| attempt.command().mission_id() == mission)
        .map(|attempt| attempt.execution_id().to_string())
        .collect();
    serde_json::json!({"schema":"roboguide.active-hazard-view/v0.1","mission_id":mission,"watches":controller.hazards.watches.iter().filter(|(id,_)| ids.contains(*id)).collect::<BTreeMap<_,_>>(),"observation_is_stop_certificate":false})
}
