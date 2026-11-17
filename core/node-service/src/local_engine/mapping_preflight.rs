//! Pure partial request evaluation; unknown runtime values never become fabricated evidence.

use super::*;
use std::collections::BTreeMap;

/// Transient JSON information, deliberately excluded from compiled catalogs and their digests.
#[derive(Clone)]
enum StaticJson {
    /// An opaque value plus fields guaranteed only if preceding target writes succeeded.
    Unknown(BTreeMap<String, Self>),
    /// A known scalar, or a generated scalar with no value yet.
    Scalar(Option<Value>),
    /// An object whose keys and individual value shapes are known.
    Object(BTreeMap<String, Self>),
    /// An array whose length and individual value shapes are known.
    Array(Vec<Self>),
}

impl StaticJson {
    /// Retains an unavailable value without inventing an object, array or scalar payload.
    fn unknown() -> Self {
        Self::Unknown(BTreeMap::new())
    }

    /// Preserves exact deployment/plan JSON without evaluating any transport or filesystem input.
    fn from_value(value: &Value) -> Self {
        match value {
            Value::Object(object) => Self::Object(
                object
                    .iter()
                    .map(|(key, value)| (key.clone(), Self::from_value(value)))
                    .collect(),
            ),
            Value::Array(array) => Self::Array(array.iter().map(Self::from_value).collect()),
            _ => Self::Scalar(Some(value.clone())),
        }
    }

    /// Produces exact JSON only when every participating value is actually known.
    fn known(&self) -> Option<Value> {
        match self {
            Self::Unknown(_) | Self::Scalar(None) => None,
            Self::Scalar(Some(value)) => Some(value.clone()),
            Self::Object(object) => object
                .iter()
                .map(|(key, value)| value.known().map(|value| (key.clone(), value)))
                .collect::<Option<Map<_, _>>>()
                .map(Value::Object),
            Self::Array(array) => array
                .iter()
                .map(Self::known)
                .collect::<Option<Vec<_>>>()
                .map(Value::Array),
        }
    }

    /// Resolves known fields; an opaque source stays unavailable rather than acquiring a value.
    fn pointer(&self, pointer: &str) -> Result<Self, MappingError> {
        if pointer.is_empty() {
            return Ok(self.clone());
        }
        let mut current = self;
        for token in pointer[1..].split('/').map(decode_token) {
            current = match current {
                Self::Unknown(children) => match children.get(&token) {
                    Some(value) => Some(value),
                    None => return Ok(Self::unknown()),
                },
                Self::Object(object) => object.get(&token),
                Self::Array(array) => token
                    .parse::<usize>()
                    .ok()
                    .and_then(|index| array.get(index)),
                Self::Scalar(_) => None,
            }
            .ok_or_else(|| MappingError::MissingSource(pointer.into()))?;
        }
        Ok(current.clone())
    }

    /// Mirrors request assignment in declaration order, refusing only provably impossible shapes.
    fn set_pointer(&mut self, pointer: &str, value: Self) -> Result<(), MappingError> {
        if pointer.is_empty() {
            *self = value;
            return Ok(());
        }
        let tokens = pointer[1..]
            .split('/')
            .map(decode_token)
            .collect::<Vec<_>>();
        let mut current = self;
        for token in &tokens[..tokens.len() - 1] {
            current = match current {
                Self::Unknown(children) => opaque_child(children, token),
                Self::Object(object) => object
                    .entry(token.clone())
                    .or_insert_with(|| Self::Object(BTreeMap::new())),
                Self::Array(array) => token
                    .parse::<usize>()
                    .ok()
                    .and_then(|index| array.get_mut(index))
                    .ok_or_else(|| MappingError::InvalidTarget(pointer.into()))?,
                Self::Scalar(_) => return Err(MappingError::InvalidTarget(pointer.into())),
            };
        }
        let last = tokens.last().expect("non-empty pointer has a final token");
        match current {
            Self::Unknown(children) => {
                forget_array_aliases(children, last);
                children.insert(last.clone(), value);
            }
            Self::Object(object) => {
                object.insert(last.clone(), value);
            }
            Self::Array(array) if last == "-" => array.push(value),
            Self::Array(array) => {
                let item = last
                    .parse::<usize>()
                    .ok()
                    .and_then(|index| array.get_mut(index))
                    .ok_or_else(|| MappingError::InvalidTarget(pointer.into()))?;
                *item = value;
            }
            Self::Scalar(_) => return Err(MappingError::InvalidTarget(pointer.into())),
        }
        Ok(())
    }
}

/// Drops facts that an opaque array write could replace through another numeric spelling/append.
fn forget_array_aliases(children: &mut BTreeMap<String, StaticJson>, token: &str) {
    if token == "-" || token.parse::<usize>().is_ok() {
        children.retain(|key, _| key == token || (key != "-" && key.parse::<usize>().is_err()));
    }
}

/// Preserves a known child only when another target spelling cannot have replaced that child.
fn opaque_child<'a>(
    children: &'a mut BTreeMap<String, StaticJson>,
    token: &str,
) -> &'a mut StaticJson {
    forget_array_aliases(children, token);
    children
        .entry(token.into())
        .or_insert_with(StaticJson::unknown)
}

/// Masks original Control/local-generated facts while retaining plan-owned fields and array counts.
fn context(invocation: &Value) -> StaticJson {
    let mut invocation = StaticJson::from_value(invocation);
    if let StaticJson::Object(object) = &mut invocation {
        object.insert("group_id".into(), StaticJson::Scalar(None));
        object.insert("execution_generation".into(), StaticJson::Scalar(None));
        for (field, scalar) in [("resource_ids", true), ("resource_grants", false)] {
            if let Some(StaticJson::Array(array)) = object.get_mut(field) {
                array.fill(if scalar {
                    StaticJson::Scalar(None)
                } else {
                    StaticJson::unknown()
                });
            }
        }
    }
    StaticJson::Object(BTreeMap::from([
        ("invocation".into(), invocation),
        ("steps".into(), StaticJson::unknown()),
        ("artifacts".into(), StaticJson::unknown()),
        ("attempt_id".into(), StaticJson::Scalar(None)),
        ("local_handle".into(), StaticJson::Scalar(None)),
    ]))
}

/// Evaluates pure known expressions and retains only guaranteed output shape for unknown inputs.
fn expression(
    value: &ValueExpressionConfig,
    context: Option<&StaticJson>,
) -> Result<StaticJson, MappingError> {
    match value {
        ValueExpressionConfig::Constant { value } => Ok(StaticJson::from_value(value)),
        ValueExpressionConfig::Pointer { pointer } => context
            .map_or(Ok(StaticJson::unknown()), |context| {
                context.pointer(pointer)
            }),
        ValueExpressionConfig::Function {
            function,
            arguments,
        } => {
            let [argument] = arguments.as_slice() else {
                return Err(MappingError::InvalidFunctionArguments(
                    function_name(*function).into(),
                ));
            };
            let argument = expression(argument, context)?;
            if let Some(value) = argument.known() {
                return apply_function(*function, value)
                    .map(|value| StaticJson::from_value(&value));
            }
            if matches!(argument, StaticJson::Object(_) | StaticJson::Array(_)) {
                return Err(MappingError::InvalidFunctionArguments(
                    function_name(*function).into(),
                ));
            }
            Ok(if *function == ValueFunction::QuaternionFromYaw {
                StaticJson::Object(
                    ["x", "y", "z", "w"]
                        .into_iter()
                        .map(|key| (key.into(), StaticJson::Scalar(None)))
                        .collect(),
                )
            } else {
                StaticJson::Scalar(None)
            })
        }
    }
}

/// Checks a fresh abstract request without running local steps or asserting any dynamic success.
pub(super) fn validate(
    mapping: &CompiledRequestMapping,
    invocation: Option<&Value>,
) -> Result<(), MappingError> {
    let context = invocation.map(context);
    let mut request = StaticJson::from_value(&mapping.base);
    for binding in &mapping.bindings {
        let value = expression(&binding.value, context.as_ref())?;
        request.set_pointer(&binding.target, value)?;
    }
    Ok(())
}
