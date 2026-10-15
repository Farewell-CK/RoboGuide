//! Real cryptographic checks over explicitly constructed software vectors; no TPM or device is simulated.

use super::*;
use crate::application::attestation;
use domain::{AttestationChallenge, AttestationEvidence, AttestationPolicy};
use sha2::{Digest, Sha256};

/// Encodes exact public vector bytes, never replacing a cryptographic verifier result.
fn encoded(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Runs a bounded owned offline OpenSSL command; production verification uses ring.
fn openssl(root: &Path, arguments: &[&str]) {
    let result = std::process::Command::new("openssl")
        .args(arguments)
        .current_dir(root)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}

/// Produces a signed TPMS_ATTEST software vector with one declared SHA-256 PCR.
fn vector(root: &Path, scheme: &str) -> (AttestationChallenge, AttestationEvidence) {
    let key_args: &[&str] = if scheme == "ecdsa-p256-sha256" {
        &[
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-out",
            "ak.key",
        ]
    } else {
        &[
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            "ak.key",
        ]
    };
    openssl(root, key_args);
    openssl(
        root,
        &["pkey", "-in", "ak.key", "-pubout", "-out", "ak.pub"],
    );
    let policy = AttestationPolicy {
        policy_id: "software-vector-policy".into(),
        revision: 1,
        ak_public_key_pem: std::fs::read_to_string(root.join("ak.pub")).unwrap(),
        qualified_signer_hex: None,
        expected_pcrs: BTreeMap::from([(7, "a5".repeat(32))]),
        firmware_version: Some(42),
        require_safe_clock: true,
        max_age_ms: 100,
        required_for_admission: true,
        provisioning_evidence_digest: None,
    };
    let challenge = AttestationChallenge {
        challenge_id: "challenge".into(),
        node_id: "software-vector".into(),
        identity_revision: 1,
        policy,
        nonce_hex: "31".repeat(32),
        issued_at_ms: 10,
        deadline_ms: 100,
        authority_subject: "offline-test".into(),
        report: None,
        evidence: None,
    };
    let mut quote = Vec::new();
    quote.extend(0xff54_4347_u32.to_be_bytes());
    quote.extend(0x8018_u16.to_be_bytes());
    quote.extend(0_u16.to_be_bytes());
    quote.extend(32_u16.to_be_bytes());
    quote.extend([0x31; 32]);
    quote.extend(500_u64.to_be_bytes());
    quote.extend(1_u32.to_be_bytes());
    quote.extend(2_u32.to_be_bytes());
    quote.push(1);
    quote.extend(42_u64.to_be_bytes());
    quote.extend(1_u32.to_be_bytes());
    quote.extend(0x000b_u16.to_be_bytes());
    quote.extend([3, 0x80, 0, 0]);
    quote.extend(32_u16.to_be_bytes());
    quote.extend(Sha256::digest([0xa5; 32]));
    std::fs::write(root.join("quote.msg"), &quote).unwrap();
    let mut sign = vec!["dgst", "-sha256", "-sign", "ak.key", "-out", "quote.sig"];
    if scheme == "rsapss-sha256" {
        sign.extend([
            "-sigopt",
            "rsa_padding_mode:pss",
            "-sigopt",
            "rsa_pss_saltlen:digest",
        ]);
    }
    sign.push("quote.msg");
    openssl(root, &sign);
    let evidence = AttestationEvidence {
        challenge_id: challenge.challenge_id.clone(),
        quote_hex: encoded(&quote),
        signature_hex: encoded(&std::fs::read(root.join("quote.sig")).unwrap()),
        signature_scheme: scheme.into(),
    };
    (challenge, evidence)
}

/// RSA PKCS1, RSA PSS and P256 signatures are independently produced by OpenSSL and verified by ring.
#[test]
fn trusted_signature_and_exact_measurements_are_verified_without_hardware_claims() {
    for scheme in ["rsassa-sha256", "rsapss-sha256", "ecdsa-p256-sha256"] {
        let directory = tempfile::tempdir().unwrap();
        let (challenge, evidence) = vector(directory.path(), scheme);
        let report = attestation::verify_quote(&challenge, &evidence, 20).unwrap();
        assert_eq!(report.hardware_identity, "HardwareEvidencePending");
        assert_eq!(
            (
                report.tpm_clock,
                report.reset_count,
                report.restart_count,
                report.firmware_version
            ),
            (500, 1, 2, 42)
        );
        assert_eq!((report.received_at_ms, report.valid_until_ms), (20, 120));
        for case in [
            "nonce",
            "pcr",
            "firmware",
            "wrong-key",
            "deadline",
            "signature",
            "truncated",
            "scheme",
        ] {
            let mut changed = challenge.clone();
            let mut proof = evidence.clone();
            match case {
                "nonce" => changed.nonce_hex = "32".repeat(32),
                "pcr" => {
                    changed.policy.expected_pcrs.insert(7, "a6".repeat(32));
                }
                "firmware" => changed.policy.firmware_version = Some(43),
                "wrong-key" => {
                    let another = tempfile::tempdir().unwrap();
                    changed.policy.ak_public_key_pem =
                        vector(another.path(), scheme).0.policy.ak_public_key_pem;
                }
                "signature" => {
                    let first = u8::from_str_radix(&proof.signature_hex[..2], 16).unwrap() ^ 1;
                    proof
                        .signature_hex
                        .replace_range(0..2, &format!("{first:02x}"));
                }
                "truncated" => proof.quote_hex.truncate(16),
                "scheme" => proof.signature_scheme = "untrusted-default".into(),
                _ => {}
            }
            assert!(
                attestation::verify_quote(
                    &changed,
                    &proof,
                    if case == "deadline" { 100 } else { 20 }
                )
                .is_err(),
                "{scheme}: {case}"
            );
        }
    }
}

/// Runs the public HTTP handler with an explicit test administrator, not a fabricated TLS handshake.
async fn request(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    path: &str,
    body: serde_json::Value,
    now: u64,
) -> (String, serde_json::Value) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let gate = Arc::new(Mutex::new(()));
    let clock = runtime::FixedClock::new(domain::TimestampMs::new(now));
    let serve = async {
        let (mut stream, _) = listener.accept().await.unwrap();
        crate::controller_http::handle_recovery_admin_test_connection(
            &mut stream,
            controller,
            log,
            &gate,
            &clock,
            None,
            None,
        )
        .await
        .unwrap();
    };
    let call = async {
        let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
        let body = serde_json::to_vec(&body).unwrap();
        let header = format!(
            "POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n",
            body.len()
        );
        stream.write_all(header.as_bytes()).await.unwrap();
        stream.write_all(&body).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        let (head, json) = response.split_once("\r\n\r\n").unwrap();
        (
            head.lines().next().unwrap().to_string(),
            serde_json::from_str(json).unwrap(),
        )
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        tokio::join!(serve, call).1
    })
    .await
    .unwrap()
}

/// Actual public challenge/evidence mutations retain nonces, proof bytes, replay response and original expiry.
#[tokio::test]
async fn public_attestation_challenge_and_evidence_survive_replay_and_restore() {
    let directory = tempfile::tempdir().unwrap();
    let (template, mut evidence) = vector(directory.path(), "rsassa-sha256");
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut registry = domain::NodeIdentityRegistry::new("attestation-deployment");
    registry
        .register("software-vector", "tenant", "domain", "a".repeat(64), 0)
        .unwrap();
    let mut control = control::ControlPlane::new();
    control
        .bootstrap_node_identity_registry(registry.clone())
        .unwrap();
    let (events, _receiver) = tokio::sync::mpsc::unbounded_channel();
    let (_service, router) = integration::GrpcIntegrationService::new_with_observer_and_identity(
        events,
        ports::NodeProtocolPolicy::default(),
        Arc::new(runtime::FixedClock::new(domain::TimestampMs::new(10))),
        ports::DiagnosticHandle::default(),
        integration::NodeIdentityPolicy::require_lifecycle(registry).unwrap(),
    );
    let controller = Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control,
            state::InMemorySharedNodeState::new(),
            log.clone(),
            router,
        ),
        orchestrator: MissionOrchestrator::new(),
        mission_admissions: BTreeMap::new(),
        memory_imports: BTreeMap::new(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        safety_stops: BTreeMap::new(),
        verifier_seen: BTreeSet::new(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }));
    let command = domain::IdentityCommand {
        command_id: "measurement-policy".into(),
        node_id: "software-vector".into(),
        expected_revision: 1,
        action: domain::IdentityAction::ConfigureAttestation {
            policy: template.policy,
        },
    };
    let configured = request(
        &controller,
        &log,
        "/v1/node-identities/commands",
        serde_json::to_value(command).unwrap(),
        10,
    )
    .await;
    assert!(configured.0.contains("200"), "{configured:?}");
    let challenge_request = serde_json::json!({"challenge_id": "fresh-challenge", "node_id": "software-vector", "expected_revision": 2, "timeout_ms": 100});
    let (status, first) = request(
        &controller,
        &log,
        "/v1/attestation/challenges",
        challenge_request.clone(),
        11,
    )
    .await;
    assert!(status.contains("200"), "{first}");
    let challenge: AttestationChallenge = serde_json::from_value(first["result"].clone()).unwrap();
    assert_ne!(challenge.nonce_hex, template.nonce_hex);
    let replay = request(
        &controller,
        &log,
        "/v1/attestation/challenges",
        challenge_request,
        50,
    )
    .await;
    assert_eq!(replay.1, first);
    let mut quote = std::fs::read(directory.path().join("quote.msg")).unwrap();
    let nonce: Vec<_> = challenge
        .nonce_hex
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect();
    quote[10..42].copy_from_slice(&nonce);
    std::fs::write(directory.path().join("quote.msg"), &quote).unwrap();
    openssl(
        directory.path(),
        &[
            "dgst",
            "-sha256",
            "-sign",
            "ak.key",
            "-out",
            "quote.sig",
            "quote.msg",
        ],
    );
    evidence.challenge_id = challenge.challenge_id;
    evidence.quote_hex = encoded(&quote);
    evidence.signature_hex = encoded(&std::fs::read(directory.path().join("quote.sig")).unwrap());
    let body = serde_json::to_value(&evidence).unwrap();
    let (status, result) = request(
        &controller,
        &log,
        "/v1/attestation/evidence",
        body.clone(),
        20,
    )
    .await;
    assert!(status.contains("200"), "{result}");
    assert_eq!(
        request(&controller, &log, "/v1/attestation/evidence", body, 500)
            .await
            .1,
        result
    );
    assert_eq!(result["result"]["received_at_ms"], 20);
    assert_eq!(result["result"]["valid_until_ms"], 120);
    assert_eq!(result["hardware_identity"], "HardwareEvidencePending");
    attestation::validate_restored(&controller.lock().unwrap()).unwrap();
    let saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        log.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(600),
    )
    .unwrap();
    let registry = restored.control().node_identity_registry().unwrap();
    assert_eq!(
        registry.records["software-vector"]
            .attestation_report
            .as_ref()
            .unwrap()
            .valid_until_ms,
        120
    );
    assert_eq!(registry.attestation_challenges.len(), 1);
    assert_eq!(controller.lock().unwrap().bridge.state().nodes().len(), 0);
    let mut corrupted = evidence;
    let original_signature = corrupted.signature_hex.clone();
    let changed = u8::from_str_radix(&corrupted.signature_hex[..2], 16).unwrap() ^ 1;
    corrupted
        .signature_hex
        .replace_range(0..2, &format!("{changed:02x}"));
    assert_ne!(corrupted.signature_hex, original_signature);
    assert!(
        request(
            &controller,
            &log,
            "/v1/attestation/evidence",
            serde_json::to_value(corrupted).unwrap(),
            21
        )
        .await
        .0
        .contains("409")
    );
}
