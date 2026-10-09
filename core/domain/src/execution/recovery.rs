//! Deployment-owned stop and continuation facts, separate from Mission and recovery authority.

use super::OperationRef;
use crate::LocalSystemId;
use std::collections::BTreeSet;

/// Versioned registration metadata key; absence never implies independent stopping or retry.
pub const EXECUTION_RECOVERY_METADATA_KEY: &str = "roboguide.execution-recovery";
/// Closed deployment profile carried through the existing LocalSystem metadata transport.
pub const EXECUTION_RECOVERY_PROFILE_SCHEMA: &str = "roboguide.local-execution-recovery/v0.1";

/// Largest physical scope affected by cancellation of one local invocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExecutionStopScope {
    /// Only the addressed physical execution stops; other executions retain their original work.
    Execution,
    /// Cancellation can stop other executions in the same accepted Execution Group.
    ExecutionGroup,
    /// The deployment cannot attest to stopping through its configured Cancel workflow.
    Unsupported,
}

/// Deployment's ability to execute the intact invocation after an actual stop.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExecutionContinuation {
    /// A fresh attempt preserves required context and prior effects, without resetting the world.
    RepeatAfterStop,
    /// Repetition after cancellation is unavailable; ordinary next-Task execution is separate.
    Unsupported,
}

/// One exact canonical operation's implementation facts; not permission to repeat its effects.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperationRecoverySupport {
    /// Exact canonical operation owned by the declaring Local EAIOS.
    pub operation: OperationRef,
    /// Actual impact of the configured cancellation workflow.
    pub stop_scope: ExecutionStopScope,
    /// Ability to preserve context for a new physical attempt after that stop.
    pub continuation: ExecutionContinuation,
}

impl OperationRecoverySupport {
    /// Reports same-Group continuation support without granting isolated stopping or repetition.
    pub const fn supports_group_continuation(&self) -> bool {
        matches!(self.stop_scope, ExecutionStopScope::ExecutionGroup)
            && matches!(self.continuation, ExecutionContinuation::RepeatAfterStop)
    }

    /// Reports technical Role retry support; explicit repeat and stop evidence are still required.
    pub const fn supports_role_retry(&self) -> bool {
        matches!(self.stop_scope, ExecutionStopScope::Execution)
            && matches!(self.continuation, ExecutionContinuation::RepeatAfterStop)
    }
}

/// Bounded, closed Local EAIOS declaration; semantic invocation support remains independent.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionRecoveryProfile {
    /// Exact declared schema, not inferred from optional fields.
    schema_version: String,
    /// At most 32 unique operation declarations owned by this same Local System.
    operations: Vec<OperationRecoverySupport>,
}

impl ExecutionRecoveryProfile {
    /// Parses a bounded profile and rejects unknown schema, malformed identities and duplicates.
    pub fn from_metadata(value: &str) -> Result<Self, String> {
        if value.len() > 8192 {
            return Err("execution recovery profile exceeds 8192 bytes".into());
        }
        let document: serde_json::Value =
            serde_json::from_str(value).map_err(|error| error.to_string())?;
        if let Some(operations) = document
            .get("operations")
            .and_then(serde_json::Value::as_array)
        {
            for support in operations {
                let operation = support
                    .get("operation")
                    .and_then(serde_json::Value::as_object)
                    .ok_or_else(|| {
                        "recovery operation must be an exact canonical object".to_string()
                    })?;
                if operation.len() != 3
                    || !["namespace", "name", "version"]
                        .iter()
                        .all(|key| operation.contains_key(*key))
                {
                    return Err(
                        "recovery operation contains unknown or missing identity fields".into(),
                    );
                }
            }
        }
        let profile: Self = serde_json::from_value(document).map_err(|error| error.to_string())?;
        if profile.schema_version != EXECUTION_RECOVERY_PROFILE_SCHEMA
            || profile.operations.is_empty()
            || profile.operations.len() > 32
        {
            return Err("execution recovery profile requires v0.1 and 1..32 operations".into());
        }
        let mut identities = BTreeSet::new();
        for support in &profile.operations {
            OperationRef::new(
                support.operation.namespace(),
                support.operation.name(),
                support.operation.version(),
            )
            .map_err(|error| error.to_string())?;
            if !identities.insert(support.operation.clone())
                || (support.stop_scope == ExecutionStopScope::Unsupported
                    && support.continuation != ExecutionContinuation::Unsupported)
            {
                return Err("execution recovery declarations are duplicate or inconsistent".into());
            }
        }
        Ok(profile)
    }

    /// Returns exact declarations for registration ownership validation, without granting authority.
    pub fn operations(&self) -> &[OperationRecoverySupport] {
        &self.operations
    }
}

/// Immutable dispatch-time owner attribution; current registration cannot upgrade an old attempt.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionRecoverySupport {
    /// Registration-local owner of the exact operation and its private Cancel workflow.
    pub local_system_id: LocalSystemId,
    /// Exact stop and continuation facts frozen for this physical attempt.
    pub support: OperationRecoverySupport,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds one exact, explicitly isolated and repeat-capable declaration.
    fn document() -> serde_json::Value {
        serde_json::json!({
            "schema_version": EXECUTION_RECOVERY_PROFILE_SCHEMA,
            "operations": [{
                "operation": {"namespace": "mobility", "name": "move", "version": "v1"},
                "stop_scope": "execution", "continuation": "repeat-after-stop"
            }]
        })
    }

    /// Coupled stop and unsupported continuation never authorize individual Role recovery.
    #[test]
    fn role_retry_requires_both_isolation_and_continuation() {
        let mut value = document();
        let profile = ExecutionRecoveryProfile::from_metadata(&value.to_string()).expect("valid");
        assert!(profile.operations()[0].supports_role_retry());
        value["operations"][0]["stop_scope"] = "execution-group".into();
        let profile = ExecutionRecoveryProfile::from_metadata(&value.to_string()).expect("valid");
        assert!(!profile.operations()[0].supports_role_retry());
        value["operations"][0]["stop_scope"] = "execution".into();
        value["operations"][0]["continuation"] = "unsupported".into();
        let profile = ExecutionRecoveryProfile::from_metadata(&value.to_string()).expect("valid");
        assert!(!profile.operations()[0].supports_role_retry());
    }

    /// Invalid or ambiguous deployment profiles fail closed before physical dispatch or recovery.
    #[test]
    fn malformed_recovery_profiles_are_rejected() {
        for pointer in [
            "/schema_version",
            "/operations/0/stop_scope",
            "/operations/0/continuation",
        ] {
            let mut value = document();
            *value.pointer_mut(pointer).expect("field") = "unknown".into();
            assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        }
        let mut value = document();
        value["extra"] = true.into();
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        let mut value = document();
        value["operations"][0]["operation"]["name"] = "".into();
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        let mut value = document();
        value["operations"][0]["operation"]["extra"] = true.into();
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        let mut value = document();
        let duplicate = value["operations"][0].clone();
        value["operations"]
            .as_array_mut()
            .expect("list")
            .push(duplicate);
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        assert!(ExecutionRecoveryProfile::from_metadata(&" ".repeat(8193)).is_err());
    }

    /// Entry and byte budgets reject the first excess item without rejecting valid boundary input.
    #[test]
    fn recovery_profile_budgets_are_exact_and_bounded() {
        let mut value = document();
        value["operations"] = serde_json::Value::Array(
            (0..32)
                .map(|index| {
                    let mut entry = document()["operations"][0].clone();
                    entry["operation"]["name"] = format!("op-{index}").into();
                    entry
                })
                .collect(),
        );
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_ok());
        value["operations"].as_array_mut().expect("entries").push({
            let mut entry = document()["operations"][0].clone();
            entry["operation"]["name"] = "excess".into();
            entry
        });
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        value["operations"] = serde_json::json!([]);
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        let mut encoded = document().to_string();
        encoded.push_str(&" ".repeat(8192 - encoded.len()));
        assert!(ExecutionRecoveryProfile::from_metadata(&encoded).is_ok());
        encoded.push(' ');
        assert!(ExecutionRecoveryProfile::from_metadata(&encoded).is_err());
        let mut value = document();
        value["operations"][0]["stop_scope"] = "unsupported".into();
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_err());
        value["operations"][0]["continuation"] = "unsupported".into();
        assert!(ExecutionRecoveryProfile::from_metadata(&value.to_string()).is_ok());
    }

    /// Registers one operation while varying which local system makes its recovery claim.
    fn registration(declaring_owner: &str) -> crate::NodeRegistration {
        let motion = LocalSystemId::new("motion").expect("owner");
        let other = LocalSystemId::new("other").expect("owner");
        crate::NodeRegistration::new_with_local_systems(
            crate::NodeId::new("node").expect("node"),
            [motion.clone(), other]
                .into_iter()
                .map(|id| {
                    let metadata = if id.as_str() == declaring_owner {
                        std::collections::BTreeMap::from([(
                            EXECUTION_RECOVERY_METADATA_KEY.to_string(),
                            document().to_string(),
                        )])
                    } else {
                        std::collections::BTreeMap::new()
                    };
                    crate::LocalSystemDescriptor::new(
                        id,
                        crate::LocalRuntime::new("fixture", "1").expect("runtime"),
                        metadata,
                    )
                })
                .collect(),
            crate::NodeContractVersion::v0_6(),
            vec![crate::Capability::new(
                crate::CapabilityKind::Mobility,
                true,
            )],
            std::collections::BTreeMap::from([(
                crate::CapabilityContractRef::new("mobility", "move", "v1").expect("contract"),
                motion,
            )]),
            Vec::new(),
            Vec::new(),
            std::collections::BTreeMap::new(),
        )
        .expect("registration")
    }

    /// An unrelated Local System cannot grant stop/continuation support for another owner.
    #[test]
    fn recovery_declaration_requires_exact_registered_operation_owner() {
        let operation = OperationRef::new("mobility", "move", "v1").expect("operation");
        let declaration = crate::OperationSupport::new(
            operation.clone(),
            LocalSystemId::new("motion").expect("owner"),
        );
        assert!(
            registration("other")
                .with_operation_support(vec![declaration.clone()])
                .is_err()
        );
        let registration = registration("motion")
            .with_operation_support(vec![declaration])
            .expect("owned profile");
        let support = registration
            .execution_recovery_support(&operation)
            .expect("declared");
        assert_eq!(support.local_system_id.as_str(), "motion");
        assert!(support.support.supports_role_retry());
        assert!(
            registration
                .execution_recovery_support(
                    &OperationRef::new("mobility", "navigate", "v1").expect("other operation")
                )
                .is_none()
        );
    }
}
