//! Durable challenge and measurement policy values; signature verification is deployment-owned.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Trusted, revisioned policy for a provisioned attestation key and exact SHA-256 PCR selection.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AttestationPolicy {
    /// Immutable policy identity.
    pub policy_id: String,
    /// Monotonic policy revision.
    pub revision: u64,
    /// Public attestation key provisioned by deployment; never supplied by quote evidence.
    pub ak_public_key_pem: String,
    /// Exact provisioned TPM qualified signer name, if available.
    pub qualified_signer_hex: Option<String>,
    /// Exact SHA-256 PCR values required by measurement policy.
    #[serde(deserialize_with = "deserialize_pcrs")]
    pub expected_pcrs: BTreeMap<u8, String>,
    /// Optional exact firmware measurement version.
    pub firmware_version: Option<u64>,
    /// Whether the TPM clock safe bit must be set.
    pub require_safe_clock: bool,
    /// Maximum receive-relative evidence validity.
    pub max_age_ms: u64,
    /// Whether current verified measurements are required before new Control commitment.
    pub required_for_admission: bool,
    /// Digest of retained external AK provisioning evidence; absence cannot establish hardware origin.
    pub provisioning_evidence_digest: Option<String>,
}

/// Normalizes canonical JSON object keys without relying on an enum content deserializer's numeric keys.
fn deserialize_pcrs<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<u8, String>, D::Error> {
    use serde::de::Error;
    BTreeMap::<String, String>::deserialize(deserializer)?
        .into_iter()
        .map(|(key, value)| {
            let pcr = key.parse::<u8>().map_err(D::Error::custom)?;
            if pcr.to_string() != key {
                return Err(D::Error::custom("PCR index is not canonical"));
            }
            Ok((pcr, value))
        })
        .collect()
}

impl AttestationPolicy {
    /// Rejects empty, unbounded or unsupported measurement policies before any challenge.
    pub fn validate(&self) -> Result<(), String> {
        if self.policy_id.trim().is_empty()
            || self.policy_id.len() > 128
            || self.revision == 0
            || self.ak_public_key_pem.len() > 8192
            || !self
                .ak_public_key_pem
                .contains("-----BEGIN PUBLIC KEY-----")
            || self.ak_public_key_pem.contains("PRIVATE KEY")
            || self.expected_pcrs.is_empty()
            || self.expected_pcrs.len() > 24
            || self.max_age_ms == 0
            || self.max_age_ms > 3_600_000
            || self
                .expected_pcrs
                .iter()
                .any(|(pcr, value)| *pcr >= 24 || !is_hex(value, 64))
            || self.qualified_signer_hex.as_ref().is_some_and(|value| {
                value.len() > 136
                    || value.is_empty()
                    || value.len() % 2 != 0
                    || !is_hex(value, value.len())
            })
            || self
                .provisioning_evidence_digest
                .as_ref()
                .is_some_and(|value| {
                    !value
                        .strip_prefix("sha256:")
                        .is_some_and(|hex| is_hex(hex, 64))
                })
        {
            return Err("invalid attestation policy".into());
        }
        Ok(())
    }
}

/// Nonrenewable exact-identity challenge retained before returning its nonce.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AttestationChallenge {
    /// Caller idempotency identity.
    pub challenge_id: String,
    /// Target Node identity.
    pub node_id: String,
    /// Exact certificate lifecycle revision.
    pub identity_revision: u64,
    /// Frozen immutable measurement policy.
    pub policy: AttestationPolicy,
    /// Cryptographically random qualification bytes in lowercase hexadecimal.
    pub nonce_hex: String,
    /// First Controller-local issue time.
    pub issued_at_ms: u64,
    /// Exclusive deadline; replays never extend it.
    pub deadline_ms: u64,
    /// Authenticated requesting administrator.
    pub authority_subject: String,
    /// Complete accepted verification report, if present.
    pub report: Option<AttestationReport>,
    /// Exact accepted quote/signature bytes retained for revalidation after restore.
    pub evidence: Option<AttestationEvidence>,
}

/// Bounded opaque TPM quote and signature supplied for one existing challenge.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AttestationEvidence {
    /// Existing challenge identity; callers cannot create a nonce by reporting evidence.
    pub challenge_id: String,
    /// Raw TPMS_ATTEST bytes as emitted by tpm2_quote -m, in hexadecimal.
    pub quote_hex: String,
    /// Plain signature bytes (RSA raw or ECDSA ASN.1) emitted by tpm2_quote -f plain.
    pub signature_hex: String,
    /// Closed algorithm profile: rsassa-sha256, rsapss-sha256 or ecdsa-p256-sha256.
    pub signature_scheme: String,
}

/// Verified signature and measurement result; it does not certify physical origin or device continuity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AttestationReport {
    /// Exact original challenge identity.
    pub challenge_id: String,
    /// Accepted immutable raw-evidence digest.
    pub evidence_digest: String,
    /// Exact provisioned public-key identity.
    pub ak_digest: String,
    /// TPM clock reported by the signed quote; never compared with Controller wall time.
    pub tpm_clock: u64,
    /// Signed TPM reset counter.
    pub reset_count: u32,
    /// Signed TPM restart counter.
    pub restart_count: u32,
    /// Signed firmware version.
    pub firmware_version: u64,
    /// Original Controller receive time of accepted verification.
    pub received_at_ms: u64,
    /// Exclusive receive-relative evidence expiry.
    pub valid_until_ms: u64,
    /// Explicit external hardware validation boundary.
    pub hardware_identity: String,
}

/// Checks a bounded canonical hexadecimal value.
pub(crate) fn is_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}
