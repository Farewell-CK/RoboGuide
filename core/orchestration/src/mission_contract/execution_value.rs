//! Conversion of Mission wire parameters into transport-neutral execution values.

use crate::OrchestrationError;
use domain::ExecutionValue;

/// Converts one JSON scalar into a canonical execution value.
pub(super) fn execution_value(
    value: serde_json::Value,
) -> Result<ExecutionValue, OrchestrationError> {
    match value {
        serde_json::Value::Bool(value) => Ok(ExecutionValue::Bool(value)),
        serde_json::Value::Number(value) if value.is_i64() => Ok(ExecutionValue::Integer(
            value
                .as_i64()
                .expect("integer JSON number validated by is_i64"),
        )),
        serde_json::Value::Number(value) => value
            .as_f64()
            .filter(|value| value.is_finite())
            .map(ExecutionValue::Float)
            .ok_or_else(|| OrchestrationError::Mission("non-finite execution number".to_string())),
        serde_json::Value::String(value) => Ok(ExecutionValue::String(value)),
        _ => Err(OrchestrationError::Mission(
            "execution parameters must be scalar".to_string(),
        )),
    }
}
