//! Semantic whole-cohort transition requirements, independent of placement and Local How.

use crate::{MissionId, OperationRef, SafetyAssurance};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Explicit transition purpose; none authorizes isolated recovery of a coupled Role.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExecutionCoordinationKind {
    /// Yield currently owned resources to a strictly higher-priority Mission.
    RunningPreemption,
    /// Replace the complete original cohort through a fresh reviewed Group.
    CoupledRecovery,
    /// Transfer original context while local systems retain safe holding/release behavior.
    Handoff,
    /// Use a deployment-owned canonical regrasp operation after original safety barriers.
    Regrasp,
}

/// Closed candidate permission input; a model cannot select Nodes, resources or stop evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionCoordinationRequest {
    /// Exact additional permission contract, never inferred from an ordinary plan switch.
    pub schema: String,
    /// Original administrative purpose.
    pub kind: ExecutionCoordinationKind,
    /// Higher-priority beneficiary only for running preemption.
    pub beneficiary_mission_id: Option<MissionId>,
    /// Explicit required observation class; software and device evidence are incomparable.
    pub stop_assurance: SafetyAssurance,
    /// Exact deployment-defined operations allowed in unfinished successor Tasks.
    #[serde(deserialize_with = "deserialize_operations")]
    pub continuation_operations: BTreeSet<OperationRef>,
    /// Bounded operator explanation; it is not an observed effect or safety fact.
    pub reason: String,
}

/// Rejects duplicate or open operation identities before normalization can erase caller input.
fn deserialize_operations<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeSet<OperationRef>, D::Error> {
    use serde::de::Error as _;
    let values: Vec<serde_json::Value> = Deserialize::deserialize(deserializer)?;
    if values.is_empty() || values.len() > 32 {
        return Err(D::Error::custom(
            "coordination requires 1..32 unique canonical operations",
        ));
    }
    let mut result = BTreeSet::new();
    for value in values {
        let operation = value
            .as_object()
            .ok_or_else(|| D::Error::custom("coordination operation must be canonical"))?;
        if operation.len() != 3
            || !["namespace", "name", "version"].iter().all(|key| {
                operation
                    .get(*key)
                    .and_then(serde_json::Value::as_str)
                    .is_some()
            })
        {
            return Err(D::Error::custom(
                "coordination operation has missing or unknown fields",
            ));
        }
        let operation = OperationRef::new(
            operation["namespace"].as_str().expect("checked identity"),
            operation["name"].as_str().expect("checked identity"),
            operation["version"].as_str().expect("checked identity"),
        )
        .map_err(D::Error::custom)?;
        if !result.insert(operation) {
            return Err(D::Error::custom("duplicate coordination operation"));
        }
    }
    Ok(result)
}

impl ExecutionCoordinationRequest {
    /// Refuses ambiguous beneficiaries, unbounded operations and software-only physical transitions.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.execution-coordination/v0.1"
            || self.reason.trim().is_empty()
            || self.reason.len() > 1024
            || self.continuation_operations.is_empty()
            || self.continuation_operations.len() > 32
            || (self.kind == ExecutionCoordinationKind::RunningPreemption)
                != self.beneficiary_mission_id.is_some()
            || matches!(
                self.kind,
                ExecutionCoordinationKind::Handoff | ExecutionCoordinationKind::Regrasp
            ) && self.stop_assurance != SafetyAssurance::DeviceObservation
        {
            return Err(
                "execution coordination requires explicit bounded purpose and assurance".into(),
            );
        }
        for operation in &self.continuation_operations {
            OperationRef::new(operation.namespace(), operation.name(), operation.version())
                .map_err(|error| error.to_string())?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a semantic unit declaration without a deployment or device inventory.
    fn request() -> ExecutionCoordinationRequest {
        ExecutionCoordinationRequest {
            schema: "roboguide.execution-coordination/v0.1".into(),
            kind: ExecutionCoordinationKind::CoupledRecovery,
            beneficiary_mission_id: None,
            stop_assurance: SafetyAssurance::SoftwareProcess,
            continuation_operations: BTreeSet::from([OperationRef::new(
                "software", "resume", "v1",
            )
            .unwrap()]),
            reason: "resume original immutable files".into(),
        }
    }

    /// Physical transitions cannot borrow software assurance or an unrelated beneficiary.
    #[test]
    fn coordination_requires_exact_purpose_and_observation_class() {
        let mut value = request();
        value.validate().unwrap();
        for kind in [
            ExecutionCoordinationKind::Handoff,
            ExecutionCoordinationKind::Regrasp,
        ] {
            value.kind = kind;
            assert!(value.validate().is_err());
            value.stop_assurance = SafetyAssurance::DeviceObservation;
            value.validate().unwrap();
            value.stop_assurance = SafetyAssurance::SoftwareProcess;
        }
        value.kind = ExecutionCoordinationKind::RunningPreemption;
        assert!(value.validate().is_err());
        value.beneficiary_mission_id = Some(MissionId::new("beneficiary").unwrap());
        value.validate().unwrap();
        value.kind = ExecutionCoordinationKind::CoupledRecovery;
        assert!(value.validate().is_err());
        value = request();
        value.continuation_operations.clear();
        assert!(value.validate().is_err());
        value = request();
        value.schema = "roboguide.execution-coordination/v0.0".into();
        assert!(value.validate().is_err());
    }

    /// Canonical command identity cannot discard duplicate operations or unrecognized nested input.
    #[test]
    fn coordination_operation_codec_refuses_normalization_aliases() {
        let mut value = serde_json::to_value(request()).unwrap();
        value["continuation_operations"][0]["ignored"] = true.into();
        assert!(serde_json::from_value::<ExecutionCoordinationRequest>(value).is_err());
        let mut value = serde_json::to_value(request()).unwrap();
        let duplicate = value["continuation_operations"][0].clone();
        value["continuation_operations"]
            .as_array_mut()
            .unwrap()
            .push(duplicate);
        assert!(serde_json::from_value::<ExecutionCoordinationRequest>(value).is_err());
    }
}
