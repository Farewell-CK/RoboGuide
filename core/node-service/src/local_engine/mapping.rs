//! Deterministic JSON mapping for canonical invocations and local workflow responses.

use crate::{RequestMappingConfig, ValueExpressionConfig, ValueFunction};
use serde_json::{Map, Number, Value};
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};

/// Immutable, validated request mapping compiled at Node Service startup.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledRequestMapping {
    /// Deployment-owned request skeleton.
    base: Value,
    /// Validated target and expression pairs in deterministic declaration order.
    bindings: Vec<CompiledBinding>,
}

/// One validated request binding.
#[derive(Debug, Clone, PartialEq)]
struct CompiledBinding {
    /// Target pointer inside the rendered request.
    target: String,
    /// Closed expression tree evaluated against workflow context.
    value: ValueExpressionConfig,
}

/// Immutable invocation plus responses accumulated by an execution workflow.
#[derive(Debug, Clone, PartialEq)]
pub struct WorkflowContext {
    /// JSON object containing `invocation` and per-step `steps` responses.
    value: Value,
}

/// Mapping validation or evaluation failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MappingError {
    /// A configured JSON Pointer is syntactically invalid.
    InvalidPointer(String),
    /// Two bindings attempt to populate the same request target.
    DuplicateTarget(String),
    /// A source pointer does not exist in the current workflow context.
    MissingSource(String),
    /// A target cannot be applied to the configured JSON shape.
    InvalidTarget(String),
    /// A conversion function received the wrong number or kind of arguments.
    InvalidFunctionArguments(String),
    /// A workflow tried to record the same step response more than once.
    DuplicateStep(String),
}

impl Display for MappingError {
    /// Formats a stable configuration or runtime mapping diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidPointer(pointer) => write!(formatter, "invalid JSON Pointer `{pointer}`"),
            Self::DuplicateTarget(pointer) => {
                write!(formatter, "duplicate request target `{pointer}`")
            }
            Self::MissingSource(pointer) => {
                write!(formatter, "mapping source `{pointer}` does not exist")
            }
            Self::InvalidTarget(pointer) => {
                write!(formatter, "request target `{pointer}` cannot be populated")
            }
            Self::InvalidFunctionArguments(function) => {
                write!(
                    formatter,
                    "invalid arguments for mapping function `{function}`"
                )
            }
            Self::DuplicateStep(step) => write!(formatter, "workflow step `{step}` already ran"),
        }
    }
}

impl std::error::Error for MappingError {}

impl CompiledRequestMapping {
    /// Validates request targets and closed value expressions once at startup.
    pub fn compile(config: RequestMappingConfig) -> Result<Self, MappingError> {
        let mut targets = BTreeSet::new();
        let mut bindings = Vec::with_capacity(config.bindings.len());
        for binding in config.bindings {
            validate_pointer(&binding.target)?;
            validate_expression(&binding.value)?;
            if !targets.insert(binding.target.clone()) {
                return Err(MappingError::DuplicateTarget(binding.target));
            }
            bindings.push(CompiledBinding {
                target: binding.target,
                value: binding.value,
            });
        }
        Ok(Self {
            base: config.base,
            bindings,
        })
    }

    /// Renders a fresh request without allowing context to alter transport routing.
    pub fn render(&self, context: &WorkflowContext) -> Result<Value, MappingError> {
        let mut request = self.base.clone();
        for binding in &self.bindings {
            let value = evaluate(&binding.value, context.as_json())?;
            set_pointer(&mut request, &binding.target, value)?;
        }
        Ok(request)
    }
}

impl WorkflowContext {
    /// Creates context for a canonical invocation before any local step has run.
    pub fn new(invocation: Value) -> Self {
        Self {
            value: serde_json::json!({
                "invocation": invocation,
                "steps": {},
                "artifacts": {
                    "inputs": {},
                    "outputs": {},
                },
            }),
        }
    }

    /// Records one complete driver response for later JSON Pointer references.
    pub fn record_step(&mut self, step_id: &str, response: Value) -> Result<(), MappingError> {
        let steps = self
            .value
            .get_mut("steps")
            .and_then(Value::as_object_mut)
            .expect("workflow context always owns a steps object");
        if steps.contains_key(step_id) {
            return Err(MappingError::DuplicateStep(step_id.to_string()));
        }
        steps.insert(step_id.to_string(), response);
        Ok(())
    }

    /// Restores a durable local execution handle for status/cancel reconciliation.
    pub fn set_local_handle(&mut self, handle: impl Into<String>) {
        let object = self
            .value
            .as_object_mut()
            .expect("workflow context is always an object");
        object.insert("local_handle".to_string(), Value::String(handle.into()));
    }

    /// Records a verified input artifact path for later declarative request mappings.
    ///
    /// The path is produced by the node-owned staging layer and cannot be selected by a
    /// remote invocation.  Binding IDs are escaped as JSON Pointer segments so a deployment
    /// may use punctuation without changing the context shape.
    pub fn set_artifact_input_path(
        &mut self,
        binding_id: &str,
        path: impl Into<String>,
    ) -> Result<(), MappingError> {
        set_artifact_path(&mut self.value, "inputs", binding_id, path.into())
    }

    /// Records a deployment-owned output artifact path for later workflow mappings.
    pub fn set_artifact_output_path(
        &mut self,
        binding_id: &str,
        path: impl Into<String>,
    ) -> Result<(), MappingError> {
        set_artifact_path(&mut self.value, "outputs", binding_id, path.into())
    }

    /// Exposes read-only workflow context to compiled mappings and state projections.
    pub const fn as_json(&self) -> &Value {
        &self.value
    }
}

/// Validates one expression recursively without evaluating deployment input.
pub(crate) fn validate_expression(expression: &ValueExpressionConfig) -> Result<(), MappingError> {
    match expression {
        ValueExpressionConfig::Pointer { pointer } => validate_pointer(pointer),
        ValueExpressionConfig::Constant { .. } => Ok(()),
        ValueExpressionConfig::Function {
            function,
            arguments,
        } => {
            if arguments.len() != 1 {
                return Err(MappingError::InvalidFunctionArguments(
                    function_name(*function).to_string(),
                ));
            }
            for argument in arguments {
                validate_expression(argument)?;
            }
            Ok(())
        }
    }
}

/// Evaluates one closed expression against current workflow facts.
pub(crate) fn evaluate(
    expression: &ValueExpressionConfig,
    context: &Value,
) -> Result<Value, MappingError> {
    match expression {
        ValueExpressionConfig::Pointer { pointer } => context
            .pointer(pointer)
            .cloned()
            .ok_or_else(|| MappingError::MissingSource(pointer.clone())),
        ValueExpressionConfig::Constant { value } => Ok(value.clone()),
        ValueExpressionConfig::Function {
            function,
            arguments,
        } => {
            let [argument] = arguments.as_slice() else {
                return Err(MappingError::InvalidFunctionArguments(
                    function_name(*function).to_string(),
                ));
            };
            apply_function(*function, evaluate(argument, context)?)
        }
    }
}

/// Applies one deterministic whitelisted conversion.
fn apply_function(function: ValueFunction, value: Value) -> Result<Value, MappingError> {
    let invalid = || MappingError::InvalidFunctionArguments(function_name(function).to_string());
    match function {
        ValueFunction::ToString => match value {
            Value::String(value) => Ok(Value::String(value)),
            Value::Bool(value) => Ok(Value::String(value.to_string())),
            Value::Number(value) => Ok(Value::String(value.to_string())),
            _ => Err(invalid()),
        },
        ValueFunction::ToInteger => {
            let integer = match value {
                Value::Number(number) => number
                    .as_i64()
                    .or_else(|| number.as_u64().and_then(|value| i64::try_from(value).ok()))
                    .or_else(|| {
                        number.as_f64().and_then(|value| {
                            (value.is_finite()
                                && value.fract() == 0.0
                                && value >= i64::MIN as f64
                                && value <= i64::MAX as f64)
                                .then_some(value as i64)
                        })
                    }),
                Value::String(value) => value.parse::<i64>().ok(),
                _ => None,
            }
            .ok_or_else(invalid)?;
            Ok(Value::Number(Number::from(integer)))
        }
        ValueFunction::ToFloat => {
            let float = match value {
                Value::Number(number) => number.as_f64(),
                Value::String(value) => value.parse::<f64>().ok(),
                _ => None,
            }
            .filter(|value| value.is_finite())
            .ok_or_else(invalid)?;
            Number::from_f64(float)
                .map(Value::Number)
                .ok_or_else(invalid)
        }
        ValueFunction::ToBoolean => match value {
            Value::Bool(value) => Ok(Value::Bool(value)),
            Value::String(value) if value.eq_ignore_ascii_case("true") => Ok(Value::Bool(true)),
            Value::String(value) if value.eq_ignore_ascii_case("false") => Ok(Value::Bool(false)),
            _ => Err(invalid()),
        },
        ValueFunction::QuaternionFromYaw => {
            let yaw = match value {
                Value::Number(number) => number.as_f64(),
                Value::String(value) => value.parse::<f64>().ok(),
                _ => None,
            }
            .filter(|value| value.is_finite())
            .ok_or_else(invalid)?;
            let half = yaw / 2.0;
            let mut quaternion = Map::new();
            quaternion.insert("x".to_string(), Value::Number(Number::from(0)));
            quaternion.insert("y".to_string(), Value::Number(Number::from(0)));
            quaternion.insert(
                "z".to_string(),
                Value::Number(Number::from_f64(half.sin()).ok_or_else(invalid)?),
            );
            quaternion.insert(
                "w".to_string(),
                Value::Number(Number::from_f64(half.cos()).ok_or_else(invalid)?),
            );
            Ok(Value::Object(quaternion))
        }
    }
}

/// Returns a stable diagnostic name for a mapping function.
const fn function_name(function: ValueFunction) -> &'static str {
    match function {
        ValueFunction::ToString => "to_string",
        ValueFunction::ToInteger => "to_integer",
        ValueFunction::ToFloat => "to_float",
        ValueFunction::ToBoolean => "to_boolean",
        ValueFunction::QuaternionFromYaw => "quaternion_from_yaw",
    }
}

/// Validates RFC 6901 escape syntax without resolving a value.
pub(crate) fn validate_pointer(pointer: &str) -> Result<(), MappingError> {
    if pointer.is_empty() {
        return Ok(());
    }
    if !pointer.starts_with('/') {
        return Err(MappingError::InvalidPointer(pointer.to_string()));
    }
    for token in pointer[1..].split('/') {
        let mut characters = token.chars();
        while let Some(character) = characters.next() {
            if character == '~' && !matches!(characters.next(), Some('0' | '1')) {
                return Err(MappingError::InvalidPointer(pointer.to_string()));
            }
        }
    }
    Ok(())
}

/// Creates or replaces an object target while respecting existing array shapes.
fn set_pointer(target: &mut Value, pointer: &str, value: Value) -> Result<(), MappingError> {
    if pointer.is_empty() {
        *target = value;
        return Ok(());
    }
    let tokens = pointer[1..]
        .split('/')
        .map(decode_token)
        .collect::<Vec<_>>();
    let mut current = target;
    for token in &tokens[..tokens.len() - 1] {
        match current {
            Value::Object(object) => {
                current = object
                    .entry(token.clone())
                    .or_insert_with(|| Value::Object(Map::new()));
            }
            Value::Array(array) => {
                let index = token
                    .parse::<usize>()
                    .ok()
                    .filter(|index| *index < array.len())
                    .ok_or_else(|| MappingError::InvalidTarget(pointer.to_string()))?;
                current = &mut array[index];
            }
            _ => return Err(MappingError::InvalidTarget(pointer.to_string())),
        }
    }
    let last = tokens.last().expect("non-empty pointer has a final token");
    match current {
        Value::Object(object) => {
            object.insert(last.clone(), value);
            Ok(())
        }
        Value::Array(array) if last == "-" => {
            array.push(value);
            Ok(())
        }
        Value::Array(array) => {
            let index = last
                .parse::<usize>()
                .ok()
                .filter(|index| *index < array.len())
                .ok_or_else(|| MappingError::InvalidTarget(pointer.to_string()))?;
            array[index] = value;
            Ok(())
        }
        _ => Err(MappingError::InvalidTarget(pointer.to_string())),
    }
}

/// Inserts a controlled artifact path under the workflow context artifact namespace.
fn set_artifact_path(
    context: &mut Value,
    direction: &str,
    binding_id: &str,
    path: String,
) -> Result<(), MappingError> {
    let escaped_binding_id = escape_pointer_segment(binding_id);
    validate_pointer(&format!("/artifacts/{direction}/{escaped_binding_id}"))?;
    let artifacts = context
        .get_mut("artifacts")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| MappingError::InvalidTarget("/artifacts".to_string()))?;
    let bindings = artifacts
        .get_mut(direction)
        .and_then(Value::as_object_mut)
        .ok_or_else(|| MappingError::InvalidTarget(format!("/artifacts/{direction}")))?;
    bindings.insert(binding_id.to_string(), Value::String(path));
    Ok(())
}

/// Escapes one caller-independent key for use as an RFC 6901 JSON Pointer segment.
fn escape_pointer_segment(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

/// Decodes one RFC 6901 path token after pointer syntax has been validated.
fn decode_token(token: &str) -> String {
    token.replace("~1", "/").replace("~0", "~")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{RequestBindingConfig, RequestMappingConfig};

    /// Mapping supports prior step output and deterministic quaternion conversion.
    #[test]
    fn renders_multistep_pointer_and_quaternion() {
        let mapping = CompiledRequestMapping::compile(RequestMappingConfig {
            base: serde_json::json!({ "goal": {}, "run": {} }),
            bindings: vec![
                RequestBindingConfig {
                    target: "/run/id".to_string(),
                    value: ValueExpressionConfig::Pointer {
                        pointer: "/steps/resolve/run_id".to_string(),
                    },
                },
                RequestBindingConfig {
                    target: "/goal/orientation".to_string(),
                    value: ValueExpressionConfig::Function {
                        function: ValueFunction::QuaternionFromYaw,
                        arguments: vec![ValueExpressionConfig::Pointer {
                            pointer: "/invocation/parameters/yaw".to_string(),
                        }],
                    },
                },
            ],
        })
        .expect("mapping compiles");
        let mut context = WorkflowContext::new(serde_json::json!({
            "parameters": { "yaw": 0.0 }
        }));
        context
            .record_step("resolve", serde_json::json!({ "run_id": "run-7" }))
            .expect("step records");
        let request = mapping.render(&context).expect("mapping renders");
        assert_eq!(
            request.pointer("/run/id"),
            Some(&Value::String("run-7".to_string()))
        );
        assert_eq!(
            request
                .pointer("/goal/orientation/w")
                .and_then(Value::as_f64),
            Some(1.0)
        );
    }

    /// Verified artifact paths are exposed under a stable, namespaced context location.
    #[test]
    fn records_artifact_paths_in_context() {
        let mut context = WorkflowContext::new(serde_json::json!({}));
        context
            .set_artifact_input_path("map~input", "/var/lib/roboguide/map.bin")
            .expect("artifact path records");
        assert_eq!(
            context.as_json().pointer("/artifacts/inputs/map~0input"),
            Some(&Value::String("/var/lib/roboguide/map.bin".to_string()))
        );
    }

    /// Invalid pointer syntax and duplicate request targets fail during compilation.
    #[test]
    fn rejects_invalid_or_duplicate_targets() {
        let duplicate = RequestMappingConfig {
            base: serde_json::json!({}),
            bindings: vec![
                RequestBindingConfig {
                    target: "/value".to_string(),
                    value: ValueExpressionConfig::Constant {
                        value: Value::Bool(true),
                    },
                },
                RequestBindingConfig {
                    target: "/value".to_string(),
                    value: ValueExpressionConfig::Constant {
                        value: Value::Bool(false),
                    },
                },
            ],
        };
        assert!(matches!(
            CompiledRequestMapping::compile(duplicate),
            Err(MappingError::DuplicateTarget(target)) if target == "/value"
        ));
        assert!(matches!(
            validate_pointer("not/a/pointer"),
            Err(MappingError::InvalidPointer(_))
        ));
    }

    /// Network-supplied values cannot turn a fixed mapping into arbitrary code evaluation.
    #[test]
    fn rejects_unknown_template_function_during_deserialization() {
        let source = r#"
            base = {}
            [[bindings]]
            target = "/danger"
            [bindings.value]
            kind = "function"
            function = "shell"
            arguments = []
        "#;
        assert!(toml::from_str::<RequestMappingConfig>(source).is_err());
    }
}
