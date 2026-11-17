//! Deterministic JSON mapping for canonical invocations and local workflow responses.

use crate::{RequestMappingConfig, ValueExpressionConfig, ValueFunction};
use serde_json::{Map, Number, Value};
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};

#[path = "mapping_preflight.rs"]
mod preflight;

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
        let compiled = Self {
            base: config.base,
            bindings,
        };
        preflight::validate(&compiled, None)?;
        Ok(compiled)
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

    /// Checks known sources, pure conversions and target shapes without reading local responses.
    pub(crate) fn invocation_sources_exist(&self, invocation: &Value) -> bool {
        self.bindings
            .iter()
            .all(|binding| invocation_source_exists(&binding.value, invocation))
            && preflight::validate(self, Some(invocation)).is_ok()
    }

    /// Returns only configured source paths, preserving nested function reads without values.
    pub(crate) fn source_pointers(&self) -> Vec<&str> {
        let mut pointers = Vec::new();
        for binding in &self.bindings {
            collect_source_pointers(&binding.value, &mut pointers);
        }
        pointers
    }
}

/// Collects recursive source paths without exposing constants or modifying the expression tree.
fn collect_source_pointers<'a>(expression: &'a ValueExpressionConfig, pointers: &mut Vec<&'a str>) {
    match expression {
        ValueExpressionConfig::Pointer { pointer } => pointers.push(pointer),
        ValueExpressionConfig::Function { arguments, .. } => {
            for argument in arguments {
                collect_source_pointers(argument, pointers);
            }
        }
        ValueExpressionConfig::Constant { .. } => {}
    }
}

/// Recurses through mappings while keeping Control-generated bindings outside offline evidence.
fn invocation_source_exists(expression: &ValueExpressionConfig, invocation: &Value) -> bool {
    match expression {
        ValueExpressionConfig::Pointer { pointer } => {
            let Some(relative) = pointer.strip_prefix("/invocation/") else {
                return true;
            };
            let field = relative.split('/').next().unwrap_or_default();
            if field == "execution_generation" {
                return true;
            }
            if field == "resource_grants" {
                // The plan determines array length; Control alone supplies the actual grants.
                let mut parts = relative.split('/');
                parts.next();
                return match parts.next() {
                    Some(index) => invocation
                        .pointer(&format!("/resource_grants/{index}"))
                        .is_some(),
                    None => invocation.get("resource_grants").is_some(),
                };
            }
            invocation.pointer(&format!("/{relative}")).is_some()
        }
        ValueExpressionConfig::Function { arguments, .. } => arguments
            .iter()
            .all(|argument| invocation_source_exists(argument, invocation)),
        ValueExpressionConfig::Constant { .. } => true,
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

    /// Exposes the durable Node attempt identity to deployment-owned workflow mappings.
    pub fn with_attempt_id(invocation: Value, attempt_id: impl Into<String>) -> Self {
        let mut context = Self::new(invocation);
        context.value["attempt_id"] = Value::String(attempt_id.into());
        context
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
                Value::Number(number) => {
                    if number.is_u64() || number.is_i64() {
                        number.as_i64()
                    } else {
                        number.as_f64().and_then(|value| {
                            (value.is_finite()
                                && value.fract() == 0.0
                                && value >= i64::MIN as f64
                                // i64::MAX rounds up to 2^63 as f64; that endpoint is invalid.
                                && value < -(i64::MIN as f64))
                                .then_some(value as i64)
                        })
                    }
                }
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

    /// Numeric target spellings alias on arrays, so opaque parents cannot retain conflicting facts.
    #[test]
    fn audit_numeric_aliases_do_not_reject_valid_dynamic_array() {
        let config = RequestMappingConfig {
            base: serde_json::json!({}),
            bindings: vec![
                RequestBindingConfig {
                    target: "".into(),
                    value: ValueExpressionConfig::Pointer {
                        pointer: "/steps/source".into(),
                    },
                },
                RequestBindingConfig {
                    target: "/0".into(),
                    value: ValueExpressionConfig::Constant {
                        value: Value::Bool(true),
                    },
                },
                RequestBindingConfig {
                    target: "/00".into(),
                    value: ValueExpressionConfig::Constant {
                        value: serde_json::json!({}),
                    },
                },
                RequestBindingConfig {
                    target: "/0/child".into(),
                    value: ValueExpressionConfig::Constant {
                        value: Value::Bool(true),
                    },
                },
            ],
        };
        let mapping =
            CompiledRequestMapping::compile(config).expect("actual array completion is valid");
        let mut context = WorkflowContext::new(serde_json::json!({}));
        context
            .record_step("source", serde_json::json!([null]))
            .unwrap();
        assert_eq!(
            mapping.render(&context).unwrap(),
            serde_json::json!([{"child":true}])
        );
    }

    /// Fixed-seed concrete completions detect unsound rejections without treating samples as proof.
    #[test]
    fn bounded_partial_evaluation_agrees_with_concrete_completions() {
        let targets = [
            "",
            "/a",
            "/a/b",
            "/0",
            "/00",
            "/+0",
            "/-",
            "/0/child",
            "/00/child",
            "/a~1b/~0",
        ];
        let values = [
            serde_json::json!({}),
            serde_json::json!([]),
            serde_json::json!([{}, {}]),
            serde_json::json!({"a":{}, "0":true, "00":{}}),
            serde_json::json!(true),
            serde_json::json!(null),
        ];
        let expressions = [
            ValueExpressionConfig::Pointer {
                pointer: "/steps/source".into(),
            },
            ValueExpressionConfig::Constant {
                value: serde_json::json!({}),
            },
            ValueExpressionConfig::Constant {
                value: serde_json::json!([{}, {}]),
            },
            ValueExpressionConfig::Constant {
                value: Value::Bool(true),
            },
            ValueExpressionConfig::Function {
                function: ValueFunction::ToString,
                arguments: vec![ValueExpressionConfig::Pointer {
                    pointer: "/steps/source".into(),
                }],
            },
        ];
        let mut seed = 0x0255_f00d_u64;
        let mut next = || {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
            (seed >> 32) as usize
        };
        let mut rejected = 0;
        let mut successful = 0;
        for _ in 0..4096 {
            let mut config = RequestMappingConfig {
                base: values[next() % values.len()].clone(),
                bindings: vec![],
            };
            let mut used = BTreeSet::new();
            for _ in 0..(2 + next() % 4) {
                let target = targets[next() % targets.len()];
                if used.insert(target) {
                    config.bindings.push(RequestBindingConfig {
                        target: target.into(),
                        value: expressions[next() % expressions.len()].clone(),
                    });
                }
            }
            let result = CompiledRequestMapping::compile(config.clone());
            rejected += usize::from(result.is_err());
            // Execute the original runtime renderer even for a statically rejected configuration.
            let raw = CompiledRequestMapping {
                base: config.base,
                bindings: config
                    .bindings
                    .into_iter()
                    .map(|binding| CompiledBinding {
                        target: binding.target,
                        value: binding.value,
                    })
                    .collect(),
            };
            for completion in &values {
                let mut context = WorkflowContext::new(serde_json::json!({}));
                context.record_step("source", completion.clone()).unwrap();
                if raw.render(&context).is_ok() {
                    successful += 1;
                    assert!(
                        result.is_ok(),
                        "false rejection {result:?}, mapping={raw:?}, completion={completion}"
                    );
                }
            }
        }
        assert!(rejected > 100 && successful > 100);
        eprintln!(
            "partial-evaluation configs=4096 completions=24576 rejected={rejected} successful-renders={successful}"
        );
    }

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

    /// The exact Runtime attempt can be mapped without changing the canonical intent.
    #[test]
    fn maps_physical_attempt_identity_into_deployment_request() {
        let mapping = CompiledRequestMapping::compile(RequestMappingConfig {
            base: serde_json::json!({"invocation": {}}),
            bindings: vec![
                RequestBindingConfig {
                    target: "/invocation".to_string(),
                    value: ValueExpressionConfig::Pointer {
                        pointer: "/invocation".to_string(),
                    },
                },
                RequestBindingConfig {
                    target: "/invocation/attempt_id".to_string(),
                    value: ValueExpressionConfig::Pointer {
                        pointer: "/attempt_id".to_string(),
                    },
                },
            ],
        })
        .expect("mapping compiles");
        let context = WorkflowContext::with_attempt_id(
            serde_json::json!({"mission_id": "mission-a"}),
            "physical-attempt-2",
        );
        assert_eq!(
            mapping.render(&context).expect("mapping renders"),
            serde_json::json!({"invocation": {
                "mission_id": "mission-a", "attempt_id": "physical-attempt-2"
            }})
        );
        assert_eq!(
            context.as_json().pointer("/invocation/mission_id"),
            Some(&serde_json::json!("mission-a"))
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

    /// Deterministically impossible targets must fail before any driver is invoked.
    #[test]
    fn preflight_audit_rejects_known_target_shape_conflicts() {
        for (base, target) in [
            (serde_json::json!({"items": []}), "/items/2"),
            (serde_json::json!({"value": "scalar"}), "/value/child"),
            (serde_json::json!(null), "/child"),
        ] {
            assert!(
                CompiledRequestMapping::compile(RequestMappingConfig {
                    base,
                    bindings: vec![RequestBindingConfig {
                        target: target.into(),
                        value: ValueExpressionConfig::Constant {
                            value: Value::Bool(true)
                        },
                    }],
                })
                .is_err(),
                "{target}"
            );
        }
    }

    /// Pure conversions of constants are fully known at compilation, including integer overflow.
    #[test]
    fn preflight_audit_rejects_known_conversion_failures() {
        for (function, value) in [
            (ValueFunction::ToFloat, serde_json::json!("not-a-number")),
            (ValueFunction::ToBoolean, serde_json::json!({"flag": true})),
            (
                ValueFunction::ToInteger,
                serde_json::json!(9223372036854775808_u64),
            ),
        ] {
            let mapping = CompiledRequestMapping::compile(RequestMappingConfig {
                base: serde_json::json!({}),
                bindings: vec![RequestBindingConfig {
                    target: "/value".into(),
                    value: ValueExpressionConfig::Function {
                        function,
                        arguments: vec![ValueExpressionConfig::Constant { value }],
                    },
                }],
            });
            assert!(mapping.is_err(), "{function:?}");
        }
    }

    /// Ordered replacement, append, escaping and opaque response objects retain runtime behavior.
    #[test]
    fn partial_preflight_preserves_valid_ordered_and_dynamic_targets() {
        let mapping = CompiledRequestMapping::compile(RequestMappingConfig {
            base: serde_json::json!({"old": "scalar"}),
            bindings: vec![
                RequestBindingConfig {
                    target: "".into(),
                    value: ValueExpressionConfig::Constant {
                        value: serde_json::json!({"items": [], "a/b": {}}),
                    },
                },
                RequestBindingConfig {
                    target: "/items/-".into(),
                    value: ValueExpressionConfig::Constant {
                        value: serde_json::json!({"value": 0}),
                    },
                },
                RequestBindingConfig {
                    target: "/items/0/value".into(),
                    value: ValueExpressionConfig::Constant {
                        value: serde_json::json!(5),
                    },
                },
                RequestBindingConfig {
                    target: "/a~1b/~0key".into(),
                    value: ValueExpressionConfig::Pointer {
                        pointer: "/steps/reply/object".into(),
                    },
                },
                RequestBindingConfig {
                    target: "/a~1b/~0key/child".into(),
                    value: ValueExpressionConfig::Constant {
                        value: serde_json::json!(true),
                    },
                },
            ],
        })
        .unwrap();
        let mut context = WorkflowContext::new(serde_json::json!({}));
        context
            .record_step("reply", serde_json::json!({"object": {"existing": 1}}))
            .unwrap();
        let rendered = mapping.render(&context).unwrap();
        assert_eq!(
            rendered.pointer("/items/0/value"),
            Some(&serde_json::json!(5))
        );
        assert_eq!(
            rendered.pointer("/a~1b/~0key/child"),
            Some(&serde_json::json!(true))
        );
    }

    /// Scalar-producing conversions cannot supply a parent for a later nested target.
    #[test]
    fn rejects_certain_late_target_conflict_after_unknown_conversion() {
        assert!(
            CompiledRequestMapping::compile(RequestMappingConfig {
                base: serde_json::json!({}),
                bindings: vec![
                    RequestBindingConfig {
                        target: "/parent".into(),
                        value: ValueExpressionConfig::Function {
                            function: ValueFunction::ToString,
                            arguments: vec![ValueExpressionConfig::Pointer {
                                pointer: "/steps/reply/value".into()
                            }],
                        }
                    },
                    RequestBindingConfig {
                        target: "/parent/child".into(),
                        value: ValueExpressionConfig::Constant {
                            value: Value::Bool(true)
                        }
                    },
                ],
            })
            .is_err()
        );
    }

    /// Prior writes retain child shapes even when the original request is opaque.
    #[test]
    fn opaque_request_cannot_hide_later_certain_target_conflict() {
        assert!(
            CompiledRequestMapping::compile(RequestMappingConfig {
                base: serde_json::json!({}),
                bindings: vec![
                    RequestBindingConfig {
                        target: "".into(),
                        value: ValueExpressionConfig::Pointer {
                            pointer: "/steps/reply".into()
                        }
                    },
                    RequestBindingConfig {
                        target: "/parent".into(),
                        value: ValueExpressionConfig::Constant {
                            value: Value::Bool(true)
                        }
                    },
                    RequestBindingConfig {
                        target: "/parent/child".into(),
                        value: ValueExpressionConfig::Constant {
                            value: Value::Bool(true)
                        }
                    },
                ],
            })
            .is_err()
        );
    }

    /// Integer conversion refuses overflow instead of rounding or saturating to another integer.
    #[test]
    fn integer_conversion_keeps_exact_bounds() {
        for value in [
            serde_json::json!(9223372036854775808_u64),
            serde_json::json!(9223372036854775808.0),
            serde_json::json!(-9223372036854777856.0),
        ] {
            assert!(apply_function(ValueFunction::ToInteger, value).is_err());
        }
        for value in [
            serde_json::json!(i64::MAX),
            serde_json::json!(i64::MIN),
            serde_json::json!(0.0),
        ] {
            assert!(apply_function(ValueFunction::ToInteger, value).is_ok());
        }
    }
}
