//! Canonical canonical capability contract identity and immutable execution intent values.

use super::ExecutionValue;
use crate::DomainError;
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Identifies one extensible RoboGuide canonical capability contract independently of local skills.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
#[serde(try_from = "CapabilityContractRefWire")]
pub struct CapabilityContractRef {
    /// Extensible operation family such as `mobility` or `compute`.
    namespace: String,
    /// Operation name within its namespace.
    name: String,
    /// Independently versioned operation semantics.
    version: String,
}

/// Carries unchecked components only long enough to invoke the canonical constructor.
#[derive(serde::Deserialize)]
struct CapabilityContractRefWire {
    /// Unchecked namespace received from serialized data.
    namespace: String,
    /// Unchecked operation name received from serialized data.
    name: String,
    /// Unchecked semantic version received from serialized data.
    version: String,
}

impl TryFrom<CapabilityContractRefWire> for CapabilityContractRef {
    type Error = DomainError;

    /// Restores a contract identity without bypassing its reversible wire-shape invariant.
    fn try_from(value: CapabilityContractRefWire) -> Result<Self, Self::Error> {
        Self::new(value.namespace, value.name, value.version)
    }
}

impl CapabilityContractRef {
    /// Creates a contract whose components round-trip through `namespace.name@version`.
    pub fn new(
        namespace: impl Into<String>,
        name: impl Into<String>,
        version: impl Into<String>,
    ) -> Result<Self, DomainError> {
        let namespace =
            contract_component(namespace.into(), "capability contract namespace", true)?;
        let name = contract_component(name.into(), "capability contract name", false)?;
        let version = contract_version(version.into())?;
        Ok(Self {
            namespace,
            name,
            version,
        })
    }

    /// Returns the extensible capability contract namespace.
    pub fn namespace(&self) -> &str {
        &self.namespace
    }

    /// Returns the capability contract name within its namespace.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Returns the operation semantic version independently of the Node Contract version.
    pub fn version(&self) -> &str {
        &self.version
    }
}

impl Display for CapabilityContractRef {
    /// Formats a stable canonical capability contract key suitable for configured adapter lookup.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "{}.{}@{}",
            self.namespace, self.name, self.version
        )
    }
}

/// Describes what one role should execute without prescribing local implementation details.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionIntent {
    /// Canonical operation translated by the target adapter or local EAIOS.
    capability_contract: CapabilityContractRef,
    /// Stable transport-neutral parameters keyed by semantic name.
    parameters: BTreeMap<String, ExecutionValue>,
}

impl ExecutionIntent {
    /// Creates an immutable intent while rejecting blank parameter keys.
    pub fn new(
        capability_contract: CapabilityContractRef,
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
            capability_contract,
            parameters,
        })
    }

    /// Returns the canonical capability contract identity.
    pub const fn capability_contract(&self) -> &CapabilityContractRef {
        &self.capability_contract
    }

    /// Returns parameters in stable lexical key order.
    pub const fn parameters(&self) -> &BTreeMap<String, ExecutionValue> {
        &self.parameters
    }
}

/// Returns a canonical contract component or a typed domain invariant error.
fn contract_component(
    value: String,
    kind: &'static str,
    allow_dots: bool,
) -> Result<String, DomainError> {
    let invalid = value.trim().is_empty()
        || value
            .chars()
            .any(|character| character.is_whitespace() || character == '@')
        || (!allow_dots && value.contains('.'))
        || (allow_dots && value.split('.').any(str::is_empty));
    if invalid {
        return Err(DomainError::InvalidMissionPlan {
            reason: format!("{kind} is not a canonical contract component"),
        });
    }
    Ok(value)
}

/// Returns a canonical contract version or a typed domain invariant error.
fn contract_version(value: String) -> Result<String, DomainError> {
    if value.trim().is_empty()
        || value
            .chars()
            .any(|character| character.is_whitespace() || character == '@')
    {
        return Err(DomainError::InvalidMissionPlan {
            reason: "capability contract version is not canonical".to_string(),
        });
    }
    Ok(value)
}
