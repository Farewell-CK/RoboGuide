//! Offline adapter/system tests of actual TLS sockets and exact-member application framing.

use super::*;
use testkit::TestPki;

/// Generates distinct short-lived leaves sharing localhost, so CA/DNS alone cannot distinguish them.
fn profiles(directory: &std::path::Path) -> Vec<ReplicaTlsConfig> {
    let pki = TestPki::new(directory, &["1", "2", "3"]).expect("offline TLS PKI generates");
    let digests: BTreeMap<_, _> = pki
        .identities
        .iter()
        .map(|(identity, leaf)| {
            let mut reader = std::io::BufReader::new(std::fs::File::open(&leaf.cert_path).unwrap());
            let certificate = rustls_pemfile::certs(&mut reader).next().unwrap().unwrap();
            (
                identity.parse::<u64>().unwrap(),
                format!("sha256:{:x}", Sha256::digest(certificate.as_ref())),
            )
        })
        .collect();
    (1..=3)
        .map(|id| {
            let leaf = &pki.identities[&id.to_string()];
            ReplicaTlsConfig {
                ca_cert_path: pki.ca_cert_path.clone(),
                cert_path: leaf.cert_path.clone(),
                key_path: leaf.key_path.clone(),
                server_name: "localhost".into(),
                peer_certificate_digests: digests.clone(),
            }
        })
        .collect()
}

/// Serves a synthetic successful fence only if the client sends application bytes after TLS.
async fn peer(profile: &ReplicaTlsConfig) -> (SocketAddr, tokio::task::JoinHandle<bool>) {
    let material = crate::tls::load(Some(profile)).unwrap().unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let Ok(mut stream) = TlsAcceptor::from(material.server).accept(stream).await else {
            return false;
        };
        let Ok(length) = stream.read_u64().await else {
            return false;
        };
        assert!(length <= MAX_FRAME_BYTES as u64);
        let mut bytes = vec![0; length as usize];
        stream.read_exact(&mut bytes).await.unwrap();
        let request: Request = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(request.target, 1);
        let reply = serde_json::to_vec(&Reply::Fence(Ok(()))).unwrap();
        stream.write_u64(reply.len() as u64).await.unwrap();
        stream.write_all(&reply).await.unwrap();
        true
    });
    (address, task)
}

/// Both real client paths reject a CA-valid wrong member before emitting any application frame.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn asynchronous_and_child_clients_pin_exact_server_member() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = profiles(directory.path());
    let client_tls = Arc::new(crate::tls::load(Some(&profiles[0])).unwrap().unwrap());
    for server_member in [2, 1] {
        let (address, server) = peer(&profiles[server_member - 1]).await;
        let client = ReplicaFenceClient::new_with_tls(
            address,
            "tls-test".into(),
            ControllerEpoch {
                term: 1,
                replica_id: 1,
            },
            profiles[0].clone(),
        )
        .unwrap();
        let result = tokio::task::spawn_blocking(move || client.check())
            .await
            .unwrap();
        assert_eq!(result.is_ok(), server_member == 1);
        assert_eq!(
            server.await.unwrap(),
            server_member == 1,
            "wrong peer received an application frame"
        );

        let (address, server) = peer(&profiles[server_member - 1]).await;
        let network = ReplicaNetwork {
            target: 1,
            node: BasicNode::new(address.to_string()),
            profile: "tls-test".into(),
            sender: 1,
            online: Arc::new(AtomicBool::new(true)),
            shared_secret: None,
            tls: Some(client_tls.clone()),
        };
        let result = network
            .exchange(
                Command::Fence(ControllerEpoch {
                    term: 1,
                    replica_id: 1,
                }),
                RPCOption::new(Duration::from_secs(3)),
            )
            .await;
        assert_eq!(result.is_ok(), server_member == 1);
        assert_eq!(server.await.unwrap(), server_member == 1);
    }
    let mut wrong_local = profiles[0].clone();
    wrong_local.cert_path = profiles[1].cert_path.clone();
    wrong_local.key_path = profiles[1].key_path.clone();
    assert!(
        ReplicaFenceClient::new_with_tls(
            "127.0.0.1:1".parse().unwrap(),
            "tls-test".into(),
            ControllerEpoch {
                term: 1,
                replica_id: 1
            },
            wrong_local
        )
        .is_err()
    );
}

/// A leaf from an unrelated CA cannot forge a successful child fence even if DNS and pin match.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn unrelated_ca_is_rejected_before_fence_frame() {
    let directory = tempfile::tempdir().unwrap();
    let mut expected = profiles(&directory.path().join("expected"));
    let foreign = profiles(&directory.path().join("foreign"));
    let mut peer_profile = foreign[0].clone();
    peer_profile.ca_cert_path = expected[0].ca_cert_path.clone();
    // The foreign server trusts the actual client, but the client does not trust its server leaf.
    expected[0]
        .peer_certificate_digests
        .insert(1, foreign[0].peer_certificate_digests[&1].clone());
    let (address, server) = peer(&peer_profile).await;
    // Keep the local identity valid while the expected target leaf is foreign: use member 2 as client.
    let mut local = expected[1].clone();
    local.peer_certificate_digests = expected[0].peer_certificate_digests.clone();
    let material = Arc::new(crate::tls::load(Some(&local)).unwrap().unwrap());
    let network = ReplicaNetwork {
        target: 1,
        node: BasicNode::new(address.to_string()),
        profile: "tls-test".into(),
        sender: 2,
        online: Arc::new(AtomicBool::new(true)),
        shared_secret: None,
        tls: Some(material),
    };
    assert!(
        network
            .exchange(
                Command::Fence(ControllerEpoch {
                    term: 1,
                    replica_id: 1
                }),
                RPCOption::new(Duration::from_secs(3))
            )
            .await
            .is_err()
    );
    assert!(!server.await.unwrap());
}

/// A still-pinned actual signed leaf cannot retain admission beyond its PKI validity interval.
#[test]
fn captured_actual_chains_expire_even_when_original_member_pins_are_unchanged() {
    let directory = tempfile::tempdir().unwrap();
    let profiles = profiles(directory.path());
    let local = crate::tls::load(Some(&profiles[0])).unwrap().unwrap();
    let bytes = std::fs::read(&profiles[1].cert_path).unwrap();
    let peer_chain = rustls_pemfile::certs(&mut &bytes[..])
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    let witness = crate::tls::VerifiedTlsConnection { local, peer_chain };
    assert!(witness.authorize(2, 1).unwrap());
    let future = rustls::pki_types::UnixTime::since_unix_epoch(Duration::from_secs(
        rustls::pki_types::UnixTime::now().as_secs() + 3 * 24 * 60 * 60,
    ));
    assert!(witness.authorize_at(2, 1, future).is_err());
    assert!(witness.authorize(2, 3).is_ok_and(|authorized| !authorized));
}

/// An actual old-key frame queued before retirement must recheck its real leaf under the writer.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn queued_actual_tls_member_cannot_observe_commands_after_its_leaf_is_retired() {
    let directory = tempfile::tempdir().unwrap();
    let pki = TestPki::new(directory.path(), &["1", "2", "3", "next"]).unwrap();
    let listeners = (0..3)
        .map(|_| std::net::TcpListener::bind("127.0.0.1:0").unwrap())
        .collect::<Vec<_>>();
    let members: BTreeMap<_, _> = listeners
        .iter()
        .enumerate()
        .map(|(index, listener)| (index as u64 + 1, listener.local_addr().unwrap()))
        .collect();
    drop(listeners);
    let mut profiles = Vec::new();
    let mut pins = BTreeMap::new();
    for member in 1..=3 {
        let identity = &pki.identities[&member.to_string()];
        let bundle = directory.path().join(format!("member-{member}.pem"));
        let mut bytes = std::fs::read(&identity.cert_path).unwrap();
        bytes.extend(std::fs::read(&identity.key_path).unwrap());
        std::fs::write(&bundle, bytes).unwrap();
        let profile = ReplicaTlsConfig {
            ca_cert_path: pki.ca_cert_path.clone(),
            cert_path: bundle.clone(),
            key_path: bundle,
            server_name: "localhost".into(),
            peer_certificate_digests: BTreeMap::new(),
        };
        let material = crate::tls::load(Some(&profile)).unwrap().unwrap();
        pins.insert(member, material.local_certificate_digest);
        profiles.push(profile);
    }
    let mut nodes = Vec::new();
    for (index, profile) in profiles.iter_mut().enumerate() {
        profile.peer_certificate_digests = pins.clone();
        let config = ReplicaConfig {
            schema: "roboguide.controller-replica/v0.3".into(),
            cluster_id: "actual-tls-queue".into(),
            replica_id: index as u64 + 1,
            members: members.clone(),
            initial_voters: Some(members.keys().copied().collect()),
            application_schema: "test/checkpoint-v1".into(),
            application_policy_digest: "original-queue-policy".into(),
            shared_secret: None,
            tls: Some(profile.clone()),
        };
        nodes.push(
            ReplicaNode::start(
                config,
                &directory.path().join(format!("member-{index}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let leader_index = crate::tests::leader(&nodes).await;
    let target_index = (leader_index + 1) % 3;
    let leader = &nodes[leader_index].0;
    let target = &nodes[target_index].0;
    let mut replacement = profiles[target_index].clone();
    replacement.cert_path = pki.identities["next"].cert_path.clone();
    replacement.key_path = pki.identities["next"].key_path.clone();
    let new_leaf = crate::tls::load(Some(&replacement))
        .unwrap()
        .unwrap()
        .local_certificate_digest;
    let stage = CertificateRequest {
        command_id: "original-actual-stage".into(),
        expected_revision: 0,
        member: target.config.replica_id,
        action: CertificateAction::Stage {
            previous_leaf: pins[&target.config.replica_id].clone(),
            new_leaf: new_leaf.clone(),
        },
    };
    let database = directory
        .path()
        .join(format!("member-{target_index}.sqlite3"));
    let admin = ReplicaAdminClient::with_trust_database(
        (*target.config).clone(),
        leader.config.replica_id,
        &database,
    )
    .unwrap();
    admin.rotate_certificate(stage.clone()).await.unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if nodes
                .iter()
                .all(|(node, _)| node.current_certificate_trust().unwrap().revision == 1)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    let old_tls = Arc::new(
        crate::tls::load(Some(&profiles[target_index]))
            .unwrap()
            .unwrap(),
    );
    let network = ReplicaNetwork {
        target: leader.config.replica_id,
        node: BasicNode::new(leader.local_address().to_string()),
        profile: leader.profile_identity(),
        sender: target.config.replica_id,
        online: Arc::new(AtomicBool::new(true)),
        shared_secret: None,
        tls: Some(old_tls),
    };
    let gate = leader.member_writer.lock().await;
    let prior = leader.framed_member_mutations.load(Ordering::SeqCst);
    let epoch = leader.current_epoch().unwrap();
    let queued = tokio::spawn(async move {
        network
            .exchange(
                Command::AdminRotateCertificate {
                    epoch,
                    request: stage,
                },
                RPCOption::new(Duration::from_secs(12)),
            )
            .await
    });
    tokio::time::timeout(Duration::from_secs(5), async {
        while leader.framed_member_mutations.load(Ordering::SeqCst) == prior {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let temporary = directory.path().join("new-credential.pending");
    let mut bytes = std::fs::read(&replacement.cert_path).unwrap();
    bytes.extend(std::fs::read(&replacement.key_path).unwrap());
    std::fs::write(&temporary, bytes).unwrap();
    std::fs::rename(temporary, &profiles[target_index].cert_path).unwrap();
    let retirement = CertificateRequest {
        command_id: "original-actual-retirement".into(),
        expected_revision: 1,
        member: target.config.replica_id,
        action: CertificateAction::Retire {
            stage_command_id: "original-actual-stage".into(),
            previous_leaf: pins[&target.config.replica_id].clone(),
            new_leaf,
        },
    };
    // The trusted composition call commits real quorum/probes while the original old-key frame waits.
    leader
        .rotate_certificate(
            epoch,
            retirement,
            leader.config.replica_id,
            &pins[&leader.config.replica_id],
        )
        .await
        .unwrap();
    drop(gate);
    let reply = queued.await.unwrap().unwrap();
    assert!(
        matches!(reply, Reply::Rejected(_)),
        "queued retired leaf entered the original command handler"
    );
    assert_eq!(
        leader.current_certificate_trust().unwrap().receipts.len(),
        2
    );
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}
