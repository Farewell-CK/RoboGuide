//! Explicit operator recovery authorization; no model may select a physical replacement here.

/// Closed command admits bounded repetition only for the named existing owner and attempt.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RecoveryCommand {
    /// Exact version separates recovery authorization from ordinary Cancel.
    pub schema_version: String,
    /// Expected current owner, not a requested replacement Node.
    pub expected_node_id: String,
    /// Explicit acknowledgement of repeating the intact operation and any prior side effects.
    pub repeat_authorized: bool,
    /// Bounded time for stop confirmation and replacement admission.
    pub timeout_ms: u64,
    /// Immutable maximum replacement authorizations for this logical Role.
    pub max_replacements: u32,
}

impl RecoveryCommand {
    /// Validates explicit semantics and bounds before opening an application write transaction.
    pub(crate) fn parse(body: &str) -> Result<Self, String> {
        let command: Self = serde_json::from_str(body).map_err(|error| error.to_string())?;
        if command.schema_version != "roboguide.execution-recovery-command/v0.1"
            || !command.repeat_authorized
            || command.expected_node_id.trim().is_empty()
            || command.expected_node_id.len() > 256
            || command.timeout_ms == 0
            || command.timeout_ms > 3_600_000
            || !(1..=16).contains(&command.max_replacements)
        {
            return Err(
                "recovery requires exact owner, explicit repeat authorization and bounded policy"
                    .into(),
            );
        }
        Ok(command)
    }
}

/// Whole current-set authorization has a separate route and schema from isolated Role recovery.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct GroupRecoveryCommand {
    /// Explicit Group command schema; it cannot be inferred from a Role command.
    pub schema_version: String,
    /// Stable request identity; resubmission never renews the original budget.
    pub recovery_id: String,
    /// Exact current attempts, original owners, and per-operation repeat permissions.
    pub members: Vec<runtime::GroupRecoveryMember>,
    /// Exclusive total stop and continuation-admission interval.
    pub timeout_ms: u64,
    /// Fixed per-Group authorization ceiling.
    pub max_replacements: u32,
}

impl GroupRecoveryCommand {
    /// Rejects unsupported schema, identity, member and budget shapes before transaction admission.
    pub(crate) fn parse(body: &str) -> Result<Self, String> {
        let command: Self = serde_json::from_str(body).map_err(|error| error.to_string())?;
        if command.schema_version != "roboguide.group-recovery-command/v0.1"
            || command.recovery_id.trim().is_empty()
            || command.recovery_id.len() > 256
            || command.members.is_empty()
            || command.members.len() > 32
            || command.members.iter().any(|member| {
                member.execution_id.trim().is_empty()
                    || member.execution_id.len() > 1024
                    || member.expected_node_id.as_str().trim().is_empty()
                    || member.expected_node_id.as_str().len() > 256
            })
            || command.timeout_ms == 0
            || command.timeout_ms > 3_600_000
            || !(1..=16).contains(&command.max_replacements)
        {
            return Err(
                "Group recovery requires exact schema and bounded complete-set authorization"
                    .into(),
            );
        }
        Ok(command)
    }
}
