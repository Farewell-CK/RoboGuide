//! Canonical operation identity and immutable execution intent values.

use super::ExecutionValue;
use crate::DomainError;
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Identifies one extensible RoboGuide canonical operation independently of local skills.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct OperationRef {
    /// Extensible operation family such as `mobility` or `compute`.
    namespace: String,
    /// Operation name within its namespace.
    name: String,
    /// Independently versioned operation semantics.
    version: String,
}

impl OperationRef {
    /// Creates an operation reference while rejecting blank identity components.
    pub fn new(
        namespace: impl Into<String>,
        name: impl Into<String>,
        version: impl Into<String>,
    ) -> Result<Self, DomainError> {
        let namespace = nonblank(namespace.into(), "operation namespace")?;
        let name = nonblank(name.into(), "operation name")?;
        let version = nonblank(version.into(), "operation version")?;
        Ok(Self {
            namespace,
            name,
            version,
        })
    }

    /// Returns the extensible operation namespace.
    pub fn namespace(&self) -> &str {
        &self.namespace
    }

    /// Returns the operation name within its namespace.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Returns the operation semantic version independently of the Node Contract version.
    pub fn version(&self) -> &str {
        &self.version
    }
}

impl Display for OperationRef {
    /// Formats a stable canonical operation key suitable for configured adapter lookup.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "{}.{}@{}",
            self.namespace, self.name, self.version
        )
    }
}

/// Describes what one role should execute without prescribing local implementation details.
#[derive(Debug, Clone, PartialEq)]
pub struct ExecutionIntent {
    /// Canonical operation translated by the target adapter or local EAIOS.
    operation: OperationRef,
    /// Stable transport-neutral parameters keyed by semantic name.
    parameters: BTreeMap<String, ExecutionValue>,
}

impl ExecutionIntent {
    /// Creates an immutable intent while rejecting blank parameter keys.
    pub fn new(
        operation: OperationRef,
        parameters: BTreeMap<String, ExecutionValue>,
    ) -> Result<Self, DomainError> {
        if parameters.keys().any(|key| key.trim().is_empty()) {
            return Err(DomainError::EmptyValue {
                kind: "execution parameter key",
            });
        }
        if parameters
            .values()
            .any(|value| matches!(value, ExecutionValue::Float(number) if !number.is_finite()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: "execution parameters must contain finite floats".to_string(),
            });
        }
        Ok(Self {
            operation,
            parameters,
        })
    }

    /// Returns the canonical operation identity.
    pub const fn operation(&self) -> &OperationRef {
        &self.operation
    }

    /// Returns parameters in stable lexical key order.
    pub const fn parameters(&self) -> &BTreeMap<String, ExecutionValue> {
        &self.parameters
    }
}

/// Returns nonblank text or a typed domain invariant error.
fn nonblank(value: String, kind: &'static str) -> Result<String, DomainError> {
    if value.trim().is_empty() {
        return Err(DomainError::EmptyValue { kind });
    }
    Ok(value)
}
