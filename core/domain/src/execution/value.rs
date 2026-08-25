//! Minimal transport-neutral execution parameter values.

/// A scalar execution parameter whose wire representation belongs to an adapter.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionValue {
    /// A binary operation option.
    Bool(bool),
    /// A signed whole-number parameter.
    Integer(i64),
    /// A floating-point parameter such as a coordinate or threshold.
    Float(f64),
    /// A textual semantic value, never an executable command supplied by the network.
    String(String),
}
