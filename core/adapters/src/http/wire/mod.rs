//! Versioned HTTP DTOs and explicit conversion to transport-neutral domain values.

mod execution;
mod registration;
mod status;

pub(crate) use execution::{WireExecutionRequest, WireExecutionResponse};
pub(crate) use registration::WireRegistration;
pub(crate) use status::WireStatus;

use crate::http::HttpAdapterError;
use domain::{ExecutionIntent, ExecutionValue, OperationRef};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// HTTP representation of a canonical operation identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct WireOperationRef {
    /// Extensible canonical operation family.
    namespace: String,
    /// Operation name within its family.
    name: String,
    /// Independently versioned operation semantics.
    version: String,
}

/// HTTP scalar representation accepted for v0.1 execution parameters.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub(super) enum WireExecutionValue {
    /// Boolean parameter.
    Bool(bool),
    /// Signed integer parameter.
    Integer(i64),
    /// Finite floating-point parameter.
    Float(f64),
    /// Text parameter.
    String(String),
}

/// HTTP representation of a canonical execution intent.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct WireExecutionIntent {
    /// Canonical operation identity.
    operation: WireOperationRef,
    /// Stable scalar parameter map.
    parameters: BTreeMap<String, WireExecutionValue>,
}

impl From<&OperationRef> for WireOperationRef {
    /// Copies domain operation identity into an HTTP-only DTO.
    fn from(operation: &OperationRef) -> Self {
        Self {
            namespace: operation.namespace().to_string(),
            name: operation.name().to_string(),
            version: operation.version().to_string(),
        }
    }
}

impl TryFrom<WireOperationRef> for OperationRef {
    type Error = HttpAdapterError;

    /// Validates wire operation identity before it enters the domain.
    fn try_from(operation: WireOperationRef) -> Result<Self, Self::Error> {
        Self::new(operation.namespace, operation.name, operation.version)
            .map_err(|error| HttpAdapterError::protocol(error.to_string()))
    }
}

impl From<&ExecutionValue> for WireExecutionValue {
    /// Copies one transport-neutral scalar into its JSON DTO representation.
    fn from(value: &ExecutionValue) -> Self {
        match value {
            ExecutionValue::Bool(value) => Self::Bool(*value),
            ExecutionValue::Integer(value) => Self::Integer(*value),
            ExecutionValue::Float(value) => Self::Float(*value),
            ExecutionValue::String(value) => Self::String(value.clone()),
        }
    }
}

impl From<WireExecutionValue> for ExecutionValue {
    /// Converts one decoded JSON scalar without retaining serde types.
    fn from(value: WireExecutionValue) -> Self {
        match value {
            WireExecutionValue::Bool(value) => Self::Bool(value),
            WireExecutionValue::Integer(value) => Self::Integer(value),
            WireExecutionValue::Float(value) => Self::Float(value),
            WireExecutionValue::String(value) => Self::String(value),
        }
    }
}

impl From<&ExecutionIntent> for WireExecutionIntent {
    /// Copies a canonical domain intent into an HTTP-only DTO.
    fn from(intent: &ExecutionIntent) -> Self {
        Self {
            operation: intent.operation().into(),
            parameters: intent
                .parameters()
                .iter()
                .map(|(key, value)| (key.clone(), value.into()))
                .collect(),
        }
    }
}

impl TryFrom<WireExecutionIntent> for ExecutionIntent {
    type Error = HttpAdapterError;

    /// Validates a decoded wire intent before it crosses into Core domain values.
    fn try_from(intent: WireExecutionIntent) -> Result<Self, Self::Error> {
        let operation = intent.operation.try_into()?;
        let parameters = intent
            .parameters
            .into_iter()
            .map(|(key, value)| (key, value.into()))
            .collect();
        Self::new(operation, parameters)
            .map_err(|error| HttpAdapterError::protocol(error.to_string()))
    }
}

/// Decodes a smoke-test intent fixture without exposing HTTP DTOs or serde values.
pub fn decode_intent_fixture(source: &str) -> Result<ExecutionIntent, HttpAdapterError> {
    let wire: WireExecutionIntent = serde_json::from_str(source)
        .map_err(|error| HttpAdapterError::protocol(error.to_string()))?;
    wire.try_into()
}
