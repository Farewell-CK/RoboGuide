//! Extensible canonical capability requirements and feasibility constraints.

use crate::{CapabilityContractRef, DomainError, ExecutionValue};
use std::collections::BTreeSet;

/// Comparison applied to one provider-declared capability attribute.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum CapabilityConstraintOperator {
    /// Require exact value equality.
    Equals,
    /// Require a numeric provider value greater than or equal to the requested value.
    AtLeast,
    /// Require a numeric provider value less than or equal to the requested value.
    AtMost,
}

/// One feasibility predicate over a named capability attribute.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct CapabilityConstraint {
    /// Catalog-defined attribute identity, including units when applicable.
    attribute: String,
    /// Comparison used by Control eligibility policy.
    operator: CapabilityConstraintOperator,
    /// Required transport-neutral value.
    value: ExecutionValue,
}

impl CapabilityConstraint {
    /// Creates a predicate while rejecting blank attribute identities.
    pub fn new(
        attribute: impl Into<String>,
        operator: CapabilityConstraintOperator,
        value: ExecutionValue,
    ) -> Result<Self, DomainError> {
        let attribute = attribute.into();
        if attribute.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "capability constraint attribute",
            });
        }
        Ok(Self {
            attribute,
            operator,
            value,
        })
    }

    /// Returns the catalog-defined attribute identity.
    pub fn attribute(&self) -> &str {
        &self.attribute
    }

    /// Returns the requested comparison.
    pub const fn operator(&self) -> CapabilityConstraintOperator {
        self.operator
    }

    /// Returns the required attribute value.
    pub const fn value(&self) -> &ExecutionValue {
        &self.value
    }

    /// Evaluates the predicate against one provider-declared value.
    pub fn is_satisfied_by(&self, actual: &ExecutionValue) -> bool {
        match self.operator {
            CapabilityConstraintOperator::Equals => actual == &self.value,
            CapabilityConstraintOperator::AtLeast => {
                compare_numbers(actual, &self.value).is_some_and(|ordering| !ordering.is_lt())
            }
            CapabilityConstraintOperator::AtMost => {
                compare_numbers(actual, &self.value).is_some_and(|ordering| !ordering.is_gt())
            }
        }
    }
}

/// One exact canonical capability plus its feasibility envelope.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct CapabilityRequirement {
    /// Exact capability identity from the deployment-independent Catalog.
    contract: CapabilityContractRef,
    /// Attribute predicates that a provider declaration must satisfy.
    constraints: Vec<CapabilityConstraint>,
}

impl CapabilityRequirement {
    /// Creates a requirement and rejects duplicate attribute predicates.
    pub fn new(
        contract: CapabilityContractRef,
        constraints: Vec<CapabilityConstraint>,
    ) -> Result<Self, DomainError> {
        let mut attributes = BTreeSet::new();
        if let Some(duplicate) = constraints
            .iter()
            .map(CapabilityConstraint::attribute)
            .find(|attribute| !attributes.insert((*attribute).to_string()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("capability {contract} repeats constraint {duplicate}"),
            });
        }
        Ok(Self {
            contract,
            constraints,
        })
    }

    /// Creates an unconstrained exact capability requirement.
    pub fn exact(contract: CapabilityContractRef) -> Self {
        Self {
            contract,
            constraints: Vec::new(),
        }
    }

    /// Returns the exact canonical capability identity.
    pub const fn contract(&self) -> &CapabilityContractRef {
        &self.contract
    }

    /// Returns feasibility predicates in declaration order.
    pub fn constraints(&self) -> &[CapabilityConstraint] {
        &self.constraints
    }
}

/// Compares scalar numeric values without coercing booleans or strings.
fn compare_numbers(left: &ExecutionValue, right: &ExecutionValue) -> Option<std::cmp::Ordering> {
    let left = match left {
        ExecutionValue::Integer(value) => *value as f64,
        ExecutionValue::Float(value) if value.is_finite() => *value,
        _ => return None,
    };
    let right = match right {
        ExecutionValue::Integer(value) => *value as f64,
        ExecutionValue::Float(value) if value.is_finite() => *value,
        _ => return None,
    };
    left.partial_cmp(&right)
}
