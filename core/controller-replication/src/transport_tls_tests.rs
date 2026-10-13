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
