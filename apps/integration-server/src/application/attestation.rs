//! Exact TPM quote verification and durable challenge authority; no TPM/device is created here.

use crate::*;
use domain::{AttestationChallenge, AttestationEvidence, AttestationReport};
use ring::rand::{SecureRandom, SystemRandom};
use ring::signature;
use sha2::{Digest, Sha256};

/// Bounded quote cursor that rejects truncation and trailing bytes.
struct Cursor<'a> {
    /// Original immutable bytes.
    bytes: &'a [u8],
    /// Next unread position.
    position: usize,
}

impl<'a> Cursor<'a> {
    /// Reads an exact range without integer overflow or allocation from untrusted lengths.
    fn take(&mut self, length: usize) -> Result<&'a [u8], String> {
        let end = self
            .position
            .checked_add(length)
            .ok_or("quote size overflow")?;
        let bytes = self
            .bytes
            .get(self.position..end)
            .ok_or("truncated quote")?;
        self.position = end;
        Ok(bytes)
    }

    /// Reads a network-order unsigned integer of at most eight bytes.
    fn number(&mut self, length: usize) -> Result<u64, String> {
        Ok(self
            .take(length)?
            .iter()
            .fold(0, |value, byte| (value << 8) | u64::from(*byte)))
    }

    /// Reads one bounded TPM2B value.
    fn sized(&mut self) -> Result<&'a [u8], String> {
        let length = usize::try_from(self.number(2)?).map_err(|error| error.to_string())?;
        self.take(length)
    }

    /// Reads one canonical DER value with an exact expected tag.
    fn der(&mut self, tag: u8) -> Result<&'a [u8], String> {
        if self.number(1)? != u64::from(tag) {
            return Err("public-key DER tag differs".into());
        }
        let first = self.number(1)?;
        let length = if first < 128 {
            usize::try_from(first).map_err(|error| error.to_string())?
        } else {
            let width = usize::try_from(first & 0x7f).map_err(|error| error.to_string())?;
            if width == 0 || width > 4 {
                return Err("unbounded DER length".into());
            }
            let encoded = self.take(width)?;
            if encoded[0] == 0 {
                return Err("noncanonical DER length".into());
            }
            let value = encoded
                .iter()
                .fold(0_usize, |value, byte| (value << 8) | usize::from(*byte));
            if value < 128 {
                return Err("noncanonical DER length".into());
            }
            value
        };
        self.take(length)
    }
}

/// Decodes a bounded canonical hex field; no caller-supplied path or command is executed.
fn hex(value: &str, bound: usize) -> Result<Vec<u8>, String> {
    if value.is_empty()
        || !value.len().is_multiple_of(2)
        || value.len() > bound * 2
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err("attestation hex field is invalid or unbounded".into());
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let text = std::str::from_utf8(pair).map_err(|error| error.to_string())?;
            u8::from_str_radix(text, 16).map_err(|error| error.to_string())
        })
        .collect()
}

/// Hashes exact immutable evidence or policy bytes.
fn digest(value: &impl Serialize) -> Result<String, String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

/// Verifies trusted AK signature, exact challenge, PCR selection/digest and signed boot measurements.
pub(crate) fn verify_quote(
    challenge: &AttestationChallenge,
    evidence: &AttestationEvidence,
    received_at_ms: u64,
) -> Result<AttestationReport, String> {
    challenge.policy.validate()?;
    if evidence.challenge_id != challenge.challenge_id
        || received_at_ms < challenge.issued_at_ms
        || received_at_ms >= challenge.deadline_ms
    {
        return Err("attestation challenge identity or original receive deadline differs".into());
    }
    let quote = hex(&evidence.quote_hex, 4096)?;
    let signed = hex(&evidence.signature_hex, 1024)?;
    let mut pem = std::io::Cursor::new(challenge.policy.ak_public_key_pem.as_bytes());
    let Some(rustls_pemfile::Item::SubjectPublicKeyInfo(spki)) =
        rustls_pemfile::read_one(&mut pem).map_err(|error| error.to_string())?
    else {
        return Err("trusted AK is not a public key".into());
    };
    if rustls_pemfile::read_one(&mut pem)
        .map_err(|error| error.to_string())?
        .is_some()
    {
        return Err("trusted AK contains multiple PEM items".into());
    }
    let mut outer = Cursor {
        bytes: spki.as_ref(),
        position: 0,
    };
    let sequence = outer.der(0x30)?;
    if outer.position != outer.bytes.len() {
        return Err("public key has trailing DER".into());
    }
    let mut public = Cursor {
        bytes: sequence,
        position: 0,
    };
    let algorithm = public.der(0x30)?;
    let key = public.der(0x03)?;
    if key.first() != Some(&0) || public.position != sequence.len() {
        return Err("public-key bit string differs".into());
    }
    let rsa_oid: &[u8] = &[6, 9, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 1, 1, 1];
    let ec_p256_oid: &[u8] = &[
        6, 7, 0x2a, 0x86, 0x48, 0xce, 0x3d, 2, 1, 6, 8, 0x2a, 0x86, 0x48, 0xce, 0x3d, 3, 1, 7,
    ];
    let verifier: &'static dyn signature::VerificationAlgorithm =
        match evidence.signature_scheme.as_str() {
            "rsassa-sha256" if algorithm == rsa_oid || algorithm == [rsa_oid, &[5, 0]].concat() => {
                &signature::RSA_PKCS1_2048_8192_SHA256
            }
            "rsapss-sha256" if algorithm == rsa_oid || algorithm == [rsa_oid, &[5, 0]].concat() => {
                &signature::RSA_PSS_2048_8192_SHA256
            }
            "ecdsa-p256-sha256" if algorithm == ec_p256_oid => &signature::ECDSA_P256_SHA256_ASN1,
            _ => return Err("unsupported attestation key/signature profile".into()),
        };
    signature::UnparsedPublicKey::new(verifier, &key[1..])
        .verify(&quote, &signed)
        .map_err(|_| "attestation signature is invalid")?;
    let mut cursor = Cursor {
        bytes: &quote,
        position: 0,
    };
    if cursor.number(4)? != 0xff54_4347 || cursor.number(2)? != 0x8018 {
        return Err("evidence is not a TPM generated quote".into());
    }
    let signer = cursor.sized()?;
    if challenge
        .policy
        .qualified_signer_hex
        .as_ref()
        .is_some_and(|expected| hex(expected, 68).map_or(true, |value| value != signer))
    {
        return Err("qualified signer differs from provisioning policy".into());
    }
    if cursor.sized()? != hex(&challenge.nonce_hex, 32)? {
        return Err("quote qualification nonce differs".into());
    }
    let clock = cursor.number(8)?;
    let reset_count = u32::try_from(cursor.number(4)?).map_err(|error| error.to_string())?;
    let restart_count = u32::try_from(cursor.number(4)?).map_err(|error| error.to_string())?;
    let safe = cursor.number(1)?;
    if safe > 1 || (challenge.policy.require_safe_clock && safe != 1) {
        return Err("TPM clock policy differs".into());
    }
    let firmware = cursor.number(8)?;
    if challenge
        .policy
        .firmware_version
        .is_some_and(|expected| firmware != expected)
    {
        return Err("firmware measurement differs".into());
    }
    if cursor.number(4)? != 1 || cursor.number(2)? != 0x000b || cursor.number(1)? != 3 {
        return Err("quote requires the exact SHA-256 PCR bank profile".into());
    }
    let selection = cursor.take(3)?;
    let selected: BTreeSet<_> = (0_u8..24)
        .filter(|pcr| selection[usize::from(*pcr / 8)] & (1 << (*pcr % 8)) != 0)
        .collect();
    if selected != challenge.policy.expected_pcrs.keys().copied().collect() {
        return Err("quote PCR selection differs from measurement policy".into());
    }
    let mut pcrs = Vec::new();
    for value in challenge.policy.expected_pcrs.values() {
        pcrs.extend(hex(value, 32)?);
    }
    let expected_pcr_digest = Sha256::digest(pcrs);
    if cursor.sized()? != &expected_pcr_digest[..] || cursor.position != quote.len() {
        return Err("quote PCR digest or trailing bytes differ".into());
    }
    Ok(AttestationReport {
        challenge_id: challenge.challenge_id.clone(),
        evidence_digest: digest(evidence)?,
        ak_digest: format!("sha256:{:x}", Sha256::digest(spki.as_ref())),
        tpm_clock: clock,
        reset_count,
        restart_count,
        firmware_version: firmware,
        received_at_ms,
        valid_until_ms: received_at_ms
            .checked_add(challenge.policy.max_age_ms)
            .ok_or("attestation expiry overflow")?,
        hardware_identity: "HardwareEvidencePending".into(),
    })
}

/// Bounded authenticated request that never accepts a caller-supplied nonce.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ChallengeRequest {
    /// Stable caller identity used for exact retry.
    challenge_id: String,
    /// Previously enrolled Node.
    node_id: String,
    /// Exact current identity revision.
    expected_revision: u64,
    /// Nonrenewable receive deadline duration.
    timeout_ms: u64,
}

/// Performs an authenticated challenge or evidence operation with durable acceptance before response.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    authority_subject: &str,
    is_challenge: bool,
) -> Result<serde_json::Value, String> {
    let _gate = event_write_gate.lock()?;
    let mut live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    let mut candidate = live.clone();
    let mut registry = candidate
        .bridge
        .control()
        .node_identity_registry()
        .ok_or("dynamic Node identity profile is required")?
        .clone();
    let now = clock.now().as_millis();
    let response = if is_challenge {
        let request: ChallengeRequest =
            serde_json::from_slice(body).map_err(|error| error.to_string())?;
        if let Some(previous) = registry.attestation_challenges.get(&request.challenge_id) {
            if previous.node_id != request.node_id
                || previous.identity_revision != request.expected_revision
                || previous.deadline_ms.checked_sub(previous.issued_at_ms)
                    != Some(request.timeout_ms)
            {
                return Err("challenge identity conflicts".into());
            }
            serde_json::to_value(previous).map_err(|error| error.to_string())?
        } else {
            if request.challenge_id.trim().is_empty()
                || request.challenge_id.len() > 128
                || request.timeout_ms == 0
                || request.timeout_ms > 300_000
                || registry.attestation_challenges.len() >= 16384
            {
                return Err("challenge bounds exceeded".into());
            }
            let identity = registry
                .records
                .get(&request.node_id)
                .ok_or("Node identity is absent")?;
            if identity.revision != request.expected_revision
                || identity.phase == domain::IdentityPhase::Revoked
            {
                return Err("challenge identity revision is not current".into());
            }
            let policy = identity
                .attestation_policy
                .clone()
                .ok_or("Node has no trusted measurement policy")?;
            let mut nonce = [0_u8; 32];
            SystemRandom::new()
                .fill(&mut nonce)
                .map_err(|_| "system entropy is unavailable")?;
            let challenge = AttestationChallenge {
                challenge_id: request.challenge_id.clone(),
                node_id: request.node_id,
                identity_revision: request.expected_revision,
                policy,
                nonce_hex: nonce.iter().map(|byte| format!("{byte:02x}")).collect(),
                issued_at_ms: now,
                deadline_ms: now
                    .checked_add(request.timeout_ms)
                    .ok_or("challenge deadline overflow")?,
                authority_subject: authority_subject.into(),
                report: None,
                evidence: None,
            };
            let response = serde_json::to_value(&challenge).map_err(|error| error.to_string())?;
            registry
                .attestation_challenges
                .insert(request.challenge_id, challenge);
            response
        }
    } else {
        let evidence: AttestationEvidence =
            serde_json::from_slice(body).map_err(|error| error.to_string())?;
        let challenge = registry
            .attestation_challenges
            .get(&evidence.challenge_id)
            .ok_or("challenge is absent")?
            .clone();
        if let Some(report) = &challenge.report {
            if report.evidence_digest != digest(&evidence)? {
                return Err("accepted attestation evidence conflicts".into());
            }
            serde_json::to_value(report).map_err(|error| error.to_string())?
        } else {
            let identity = registry
                .records
                .get_mut(&challenge.node_id)
                .ok_or("Node identity is absent")?;
            if identity.revision != challenge.identity_revision
                || identity.attestation_policy.as_ref() != Some(&challenge.policy)
                || identity.phase == domain::IdentityPhase::Revoked
            {
                return Err("attestation identity or measurement policy changed".into());
            }
            let report = verify_quote(&challenge, &evidence, now)?;
            if registry
                .attestation_challenges
                .values()
                .filter_map(|previous| previous.report.as_ref())
                .filter(|previous| previous.ak_digest == report.ak_digest)
                .any(|previous| {
                    (report.reset_count, report.restart_count, report.tpm_clock)
                        < (
                            previous.reset_count,
                            previous.restart_count,
                            previous.tpm_clock,
                        )
                })
            {
                return Err("signed TPM boot/clock measurements rolled back".into());
            }
            identity.attestation_report = Some(report.clone());
            identity.revision = identity
                .revision
                .checked_add(1)
                .ok_or("identity revision overflow")?;
            let saved = registry
                .attestation_challenges
                .get_mut(&evidence.challenge_id)
                .expect("checked challenge");
            saved.report = Some(report.clone());
            saved.evidence = Some(evidence);
            serde_json::to_value(report).map_err(|error| error.to_string())?
        }
    };
    candidate
        .bridge
        .control_mut()
        .replace_attestation_registry(registry)
        .map_err(|error| error.to_string())?;
    let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
    event_log.begin_batch().map_err(|error| error.to_string())?;
    if let Err(error) =
        save_controller_checkpoint(event_log, &checkpoint).and_then(|()| event_log.commit_batch())
    {
        let rollback = event_log.rollback_batch();
        return Err(format!(
            "attestation commit failed: {error}; rollback={rollback:?}"
        ));
    }
    *live = candidate;
    live.bridge.publish_node_identity_registry()?;
    Ok(
        serde_json::json!({"schema_version": "roboguide.attestation-result/v0.1", "result": response, "hardware_identity": "HardwareEvidencePending"}),
    )
}

/// Rechecks original proof bytes and times after restore; expiry remains unchanged.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    let Some(registry) = controller.bridge.control().node_identity_registry() else {
        return Ok(());
    };
    for (id, challenge) in &registry.attestation_challenges {
        if id != &challenge.challenge_id
            || challenge.identity_revision == 0
            || challenge.issued_at_ms >= challenge.deadline_ms
            || hex(&challenge.nonce_hex, 32)?.len() != 32
            || challenge.authority_subject.is_empty()
        {
            return Err("invalid restored attestation challenge".into());
        }
        match (&challenge.report, &challenge.evidence) {
            (Some(report), Some(evidence))
                if verify_quote(challenge, evidence, report.received_at_ms)? == *report => {}
            (None, None) => {}
            _ => return Err("restored attestation proof is detached or altered".into()),
        }
    }
    for record in registry.records.values() {
        if let Some(report) = &record.attestation_report {
            let challenge = registry
                .attestation_challenges
                .get(&report.challenge_id)
                .ok_or("Node measurement report lacks its original challenge")?;
            if challenge.node_id != record.node_id
                || challenge.report.as_ref() != Some(report)
                || record.attestation_policy.as_ref() != Some(&challenge.policy)
            {
                return Err("Node measurement authority is detached".into());
            }
        }
    }
    Ok(())
}
