//! Mission-declared evidence policy for accepting Task semantic satisfaction.

/// Evidence basis that Orchestration may use to declare a Task satisfied.
///
/// The bootstrap supports only successful aggregate execution reports. This is an explicit
/// policy choice and must not be interpreted as independent physical-world verification.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum TaskSatisfactionBasis {
    /// Accept successful terminal reports from every current Role execution.
    #[default]
    ExecutionReport,
}
