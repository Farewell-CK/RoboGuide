//! Durable cross-domain prepare/commit session contract.

use domain::TimestampMs;

/// Versioned state of one cross-domain transaction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FederationSessionState {
    /// Source and target domains have durably retained the exact transaction input.
    Prepared,
    /// Both domains have committed the exact input and dispatch may be considered.
    Committed,
    /// The prepared transaction was explicitly aborted before dispatch.
    Aborted,
}

/// Exact cross-domain transaction identity and immutable ownership evidence.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FederationSession {
    /// Stable idempotency identity.
    pub transaction_id: String,
    /// Issuing domain identity.
    pub source_domain: String,
    /// Receiving domain identity.
    pub target_domain: String,
    /// Immutable tenant whose source and target authorities own this transaction.
    #[serde(default)]
    pub tenant_id: String,
    /// Source epoch; a changed epoch cannot commit the prepared session.
    pub source_epoch: u64,
    /// Exact accepted plan identity.
    pub plan_digest: String,
    /// Exact Control ownership/reservation identity.
    pub ownership_digest: String,
    /// Nonrenewable local receive-time deadline.
    pub deadline: TimestampMs,
    /// Current durable phase.
    pub state: FederationSessionState,
}

impl FederationSession {
    /// Validates bounded domain, digest, epoch and deadline identity.
    pub fn validate_at(&self, now: TimestampMs) -> Result<(), String> {
        if self.transaction_id.trim().is_empty()
            || self.transaction_id.len() > 256
            || self.source_domain.trim().is_empty()
            || self.source_domain.len() > 128
            || self.target_domain.trim().is_empty()
            || self.target_domain.len() > 128
            || self.source_domain == self.target_domain
            || self.tenant_id.trim().is_empty()
            || self.tenant_id.len() > 128
            || self.source_epoch == 0
            || !valid_digest(&self.plan_digest)
            || !valid_digest(&self.ownership_digest)
            || self.deadline <= now
        {
            return Err("federation session identity or deadline is invalid".into());
        }
        Ok(())
    }
}

/// Checks one lowercase SHA-256 identity without accepting arbitrary provider text.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
}
