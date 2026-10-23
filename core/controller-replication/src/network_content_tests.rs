//! Actual infrastructure Raft/mTLS/filesystem adapter tests; zero embodied Nodes/devices/models.

use super::*;
use sha2::Sha256;
use std::net::TcpListener;

/// Holds a real non-ephemeral endpoint until its declared infrastructure service starts.
fn reserved_listener() -> TcpListener {
    let range = std::fs::read_to_string("/proc/sys/net/ipv4/ip_local_port_range").unwrap();
    let bounds = range
        .split_whitespace()
        .map(|value| value.parse::<u16>().unwrap())
        .collect::<Vec<_>>();
    for port in 1024..=u16::MAX {
        if !(bounds[0]..=bounds[1]).contains(&port)
            && let Ok(listener) = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port))
        {
            return listener;
        }
    }
    panic!("actual test kernel has no available non-ephemeral endpoint")
}

/// Owns three actual infrastructure services and their original roots; no device inventory exists.
struct Cluster {
    /// Actual original writable temporary data, retained until all services/roots close.
    directory: tempfile::TempDir,
    /// Actual live Raft/TLS services with independent original SQL and root identities.
    nodes: Vec<ReplicaNode>,
    /// Actual accept-loop ownership, retained independently of the Node handles.
    servers: Vec<ReplicaServer>,
    /// Original managed byte roots; opening another writer remains fenced.
    roots: Vec<ReplicaContentStore>,
}

/// Starts actual mTLS members from explicit software configuration and real held root identities.
async fn cluster() -> Cluster {
    let directory = tempfile::tempdir().unwrap();
    let pki = testkit::TestPki::new(&directory.path().join("tls"), &["53", "71", "89"]).unwrap();
    let mut pins = BTreeMap::new();
    let mut bundles = BTreeMap::new();
    for (id, leaf) in &pki.identities {
        let raw = std::fs::read(&leaf.cert_path).unwrap();
        let certificate = rustls_pemfile::certs(&mut &raw[..])
            .next()
            .unwrap()
            .unwrap();
        let member = id.parse::<u64>().unwrap();
        pins.insert(
            member,
            format!("sha256:{:x}", Sha256::digest(certificate.as_ref())),
        );
        let bundle = directory.path().join(format!("actual-member-{member}.pem"));
        let mut pem = raw;
        pem.extend(std::fs::read(&leaf.key_path).unwrap());
        std::fs::write(&bundle, pem).unwrap();
        bundles.insert(member, bundle);
    }
    let mut reservations = BTreeMap::new();
    for member in pins.keys() {
        reservations.insert(*member, reserved_listener());
    }
    let members = reservations
        .iter()
        .map(|(member, listener)| (*member, listener.local_addr().unwrap()))
        .collect::<BTreeMap<_, _>>();
    let mut configs = Vec::new();
    let mut roots = Vec::new();
    for member in members.keys() {
        let config = ReplicaConfig {
            schema: "roboguide.controller-replica/v0.4".into(),
            cluster_id: format!(
                "actual-content-{}",
                directory.path().file_name().unwrap().to_str().unwrap()
            ),
            replica_id: *member,
            members: members.clone(),
            initial_voters: Some(members.keys().copied().collect()),
            application_schema: state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA.into(),
            application_policy_digest: "explicit-offline-infrastructure-contract".into(),
            shared_secret: None,
            tls: Some(ReplicaTlsConfig {
                ca_cert_path: pki.ca_cert_path.clone(),
                cert_path: bundles[member].clone(),
                key_path: bundles[member].clone(),
                server_name: "localhost".into(),
                peer_certificate_digests: pins.clone(),
            }),
        };
        roots.push(
            ReplicaContentStore::create_with_streams(
                &directory.path().join(format!("root-{member}")),
                &config.profile_identity(),
                *member,
            )
            .unwrap(),
        );
        configs.push(config);
    }
    let owners = roots
        .iter()
        .map(|root| (root.identity().member, root.identity().store_owner.clone()))
        .collect::<BTreeMap<_, _>>();
    let mut nodes = Vec::new();
    let mut servers = Vec::new();
    for (config, root) in configs.into_iter().zip(&roots) {
        drop(reservations.remove(&config.replica_id));
        let (node, server) = ReplicaNode::start_with_content(
            config.clone(),
            &directory
                .path()
                .join(format!("parent-{}.sqlite3", config.replica_id)),
            root.clone(),
            owners.clone(),
            ReplicaTransportLimits::default(),
        )
        .await
        .unwrap();
        nodes.push(node);
        servers.push(server);
    }
    Cluster {
        directory,
        nodes,
        servers,
        roots,
    }
}

/// Waits only for actual current quorum; an observed leader label alone never satisfies this gate.
async fn leader(cluster: &Cluster) -> usize {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            for (index, node) in cluster.nodes.iter().enumerate() {
                if let Some(epoch) = node.current_epoch()
                    && node.check_epoch(epoch).await.is_ok()
                {
                    return index;
                }
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap()
}

/// Actual byte streams bind both same-ID receivers independently and never reread after duplicate Ready.
#[tokio::test(flavor = "multi_thread", worker_threads = 1)]
async fn actual_mtls_content_streams_keep_original_receipts_and_independent_receivers() {
    let cluster = cluster().await;
    let index = leader(&cluster).await;
    let node = &cluster.nodes[index];
    let payload = (0..(1024 * 1024 + 17))
        .map(|index| (index % 251) as u8)
        .collect::<Vec<_>>();
    let (source, cas, key) =
        crate::content_application_tests::original_payload(cluster.directory.path(), &payload);
    let mut candidate = crate::content_application_tests::candidate(
        &cluster.directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &node.profile_identity(),
    );
    candidate.epoch = node.current_epoch().unwrap();
    let target = node.content_target_for(&candidate).await.unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        while cluster
            .nodes
            .iter()
            .any(|peer| peer.network_target_authority(&target).is_err())
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let root = cluster.roots[index].clone();
    assert_eq!(
        node.stage_content_from_source(&candidate, &cas, "actual-original-source-stage", 30000,)
            .await
            .unwrap(),
        target
    );
    let mut outcomes = Vec::new();
    for peer in cluster
        .nodes
        .iter()
        .filter(|peer| peer.config.replica_id != node.config.replica_id)
    {
        let original = node
            .stream_content_to(
                peer.config.replica_id,
                target.clone(),
                key.clone(),
                "actual-same-receive-command".into(),
                30000,
            )
            .await
            .unwrap();
        assert!(original.receipt.is_some());
        let peer_root = peer.store.network_content_store().unwrap();
        peer_root.verify_content(&key).unwrap();
        outcomes.push((peer.config.replica_id, original));
    }
    let source_path = root
        .root()
        .join("content-v1")
        .join(&key.source_owner[15..])
        .join(&key.content_digest[7..]);
    let hidden = source_path.with_extension("actual-original-held");
    std::fs::rename(&source_path, &hidden).unwrap();
    for (receiver, original) in &outcomes {
        assert_eq!(
            node.stream_content_to(
                *receiver,
                target.clone(),
                key.clone(),
                "actual-same-receive-command".into(),
                30000,
            )
            .await
            .unwrap(),
            *original
        );
    }
    std::fs::rename(hidden, source_path).unwrap();
    assert!(
        node.commit_content(candidate.clone())
            .await
            .unwrap()
            .accepted
    );
    assert_eq!(node.content_quorum(target.clone()).await.unwrap().len(), 3);
    let source_member = outcomes[0].0;
    let source_node = cluster
        .nodes
        .iter()
        .find(|peer| peer.config.replica_id == source_member)
        .unwrap();
    assert!(
        source_node
            .stream_content_to(
                node.config.replica_id,
                target.clone(),
                key.clone(),
                "actual-follower-proposal-refused".into(),
                30000,
            )
            .await
            .is_err()
    );
    let mut changed_target = target.clone();
    changed_target.revision += 1;
    assert!(
        source_node
            .route_committed_content_to(
                node.config.replica_id,
                changed_target,
                key.clone(),
                "actual-uncommitted-route-refused".into(),
                30000,
            )
            .await
            .is_err()
    );
    let leader_root_path = root
        .root()
        .join("content-v1")
        .join(&key.source_owner[15..])
        .join(&key.content_digest[7..]);
    let leader_hidden = leader_root_path.with_extension("actual-cross-owner-route");
    std::fs::rename(&leader_root_path, &leader_hidden).unwrap();
    let routed = source_node
        .route_committed_content_to(
            node.config.replica_id,
            target.clone(),
            key.clone(),
            "actual-cross-owner-root-route".into(),
            30000,
        )
        .await
        .unwrap();
    assert!(routed.receipt.is_some());
    assert_eq!(routed.original["intent"]["origin"]["member"], source_member);
    root.verify_content(&key).unwrap();
    std::fs::remove_file(&leader_root_path).ok();
    std::fs::rename(leader_hidden, leader_root_path).unwrap();
    let mut hidden_peers = Vec::new();
    for (receiver, _) in &outcomes {
        let peer = cluster
            .nodes
            .iter()
            .find(|peer| peer.config.replica_id == *receiver)
            .unwrap();
        let peer_root = peer.store.network_content_store().unwrap();
        let path = peer_root
            .root()
            .join("content-v1")
            .join(&key.source_owner[15..])
            .join(&key.content_digest[7..]);
        let hidden = path.with_extension("actual-withheld-byte-evidence");
        std::fs::rename(&path, &hidden).unwrap();
        hidden_peers.push((path, hidden));
        let result = node.content_quorum(target.clone()).await;
        if hidden_peers.len() == 1 {
            assert_eq!(result.unwrap().len(), 2);
        } else {
            assert!(
                result.is_err(),
                "metadata alone falsely granted a content quorum"
            );
        }
    }
    for (path, hidden) in hidden_peers {
        std::fs::rename(hidden, path).unwrap();
    }
    assert_eq!(node.content_quorum(target.clone()).await.unwrap().len(), 3);
    let mut changed = target.clone();
    changed.member_stores.insert(
        outcomes[0].0,
        "replica-content:".to_string() + &"f".repeat(64),
    );
    assert!(
        node.stream_content_to(
            outcomes[0].0,
            changed,
            key.clone(),
            "actual-wrong-owner".into(),
            30000
        )
        .await
        .is_err()
    );
    assert!(
        node.commit(target.epoch, candidate.batch.clone())
            .await
            .is_err()
    );
    source.begin_batch().unwrap();
    source
        .save_checkpoint(
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            r#"{"original":2}"#,
        )
        .unwrap();
    source.commit_batch().unwrap();
    let mut next = crate::content_application_tests::candidate(
        &cluster.directory.path().join("source.sqlite3"),
        &cas,
        &node.accepted_view().unwrap(),
        &node.profile_identity(),
    );
    next.epoch = node.current_epoch().unwrap();
    assert!(node.commit_content(next).await.unwrap().accepted);
    assert!(
        node.store
            .require_committed_content_target(&target)
            .is_err()
    );
    node.store
        .require_current_content_read(&target, &key, true)
        .unwrap();
    let old_child = ReplicaFenceClient::new_with_tls(
        node.local_address(),
        node.profile_identity(),
        target.epoch,
        node.config.tls.clone().unwrap(),
    )
    .unwrap();
    let refused = tokio::task::spawn_blocking(move || old_child.check())
        .await
        .unwrap()
        .unwrap_err();
    assert!(refused.contains("original child ingress contract"));
    drop(source);
    for service in &cluster.nodes {
        service.raft.shutdown().await.unwrap();
    }
    assert_eq!(cluster.servers.len(), 3);
}
