//! Upstream storage conformance plus actual three-member loopback quorum/failure checks.

use super::*;
use openraft::storage::RaftStateMachine;
use openraft::{CommittedLeaderId, Entry, EntryPayload, LogId, RaftSnapshotBuilder};
use std::sync::atomic::Ordering;
use store::io_error;

/// Creates a fresh real SQLite store for each upstream storage scenario.
struct StoreBuilder;

impl
    openraft::testing::StoreBuilder<
        ControllerRaftConfig,
        SqliteRaftStore,
        SqliteRaftStore,
        tempfile::TempDir,
    > for StoreBuilder
{
    /// Retains the private directory until both consensus storage responsibilities are dropped.
    async fn build(
        &self,
    ) -> Result<(tempfile::TempDir, SqliteRaftStore, SqliteRaftStore), openraft::StorageError<u64>>
    {
        let directory = tempfile::tempdir().map_err(io_error)?;
        let store = SqliteRaftStore::open(
            &directory.path().join("replica.sqlite3"),
            "test-cluster",
            "test/checkpoint-v1",
        )
        .map_err(io_error)?;
        Ok((directory, store.clone(), store))
    }
}

/// Runs the mature engine's full independent log/vote/membership/snapshot storage suite.
#[test]
fn upstream_storage_conformance() {
    openraft::testing::Suite::test_all(StoreBuilder).unwrap();
}

/// Builds exact typed evidence without creating a physical Node or lifecycle transition.
fn batch(
    previous: Option<&state::PersistedCheckpoint>,
    revision: u64,
) -> state::ApplicationCommitBatch {
    let tail = previous.map_or(0, |checkpoint| checkpoint.event_sequence);
    state::ApplicationCommitBatch {
        schema: state::APPLICATION_COMMIT_SCHEMA.into(),
        previous_event_sequence: tail,
        previous_checkpoint_digest: previous.map(state::checkpoint_identity),
        checkpoint: state::PersistedCheckpoint { schema:"test/checkpoint-v1".into(), event_sequence:tail + 1,
            checkpoint_json:serde_json::json!({"revision":revision,"unknown":"retained","ownership":"held","received_at":3}).to_string() },
        events:vec![state::PersistedEvent { sequence:tail + 1, event_id:format!("event-{}", tail + 1), timestamp_ms:3,
            correlation_id:"mission".into(),causation_id:None,payload_schema:"domain.EventPayload.json/v16".into(),
            payload_json:serde_json::json!({"MissionSchedulingPriorityChanged":{"mission_id":"mission","priority":17}}).to_string() }],
    }
}

/// Direct apply/snapshot/reopen never refreshes evidence, loses history or admits a mismatching predecessor.
#[tokio::test]
async fn accepted_history_snapshot_and_identity_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("replica.sqlite3");
    let mut store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let first = batch(None, 1);
    let accepted = store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
            payload: EntryPayload::Normal(first.clone()),
        }])
        .await
        .unwrap();
    assert!(accepted[0].accepted);
    let rejected = store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 2),
            payload: EntryPayload::Normal(first.clone()),
        }])
        .await
        .unwrap();
    assert!(!rejected[0].accepted);
    assert_eq!(store.accepted_view().unwrap().revision, 1);
    let snapshot = store.build_snapshot().await.unwrap();
    let mut target = SqliteRaftStore::open(
        &directory.path().join("copy.sqlite3"),
        "identity",
        "test/checkpoint-v1",
    )
    .unwrap();
    target
        .install_snapshot(&snapshot.meta, snapshot.snapshot)
        .await
        .unwrap();
    assert_eq!(
        target.accepted_view().unwrap().checkpoint,
        Some(first.checkpoint.clone())
    );
    assert_eq!(target.accepted_view().unwrap().events, first.events);
    assert!(
        SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").is_err(),
        "concurrent writer rejected"
    );
    drop(store);
    assert!(SqliteRaftStore::open(&path, "wrong-cluster", "test/checkpoint-v1").is_err());
    let reopened = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    assert_eq!(
        reopened.accepted_view().unwrap().checkpoint,
        Some(first.checkpoint)
    );
}

/// Acquires three simultaneous ephemeral listeners so their released loopback ports are distinct.
fn members() -> BTreeMap<u64, SocketAddr> {
    let listeners = (0..3)
        .map(|_| std::net::TcpListener::bind("127.0.0.1:0").unwrap())
        .collect::<Vec<_>>();
    listeners
        .iter()
        .enumerate()
        .map(|(index, listener)| (index as u64 + 1, listener.local_addr().unwrap()))
        .collect()
}

/// Supplies one exact homogeneous policy for isolated real replica engines.
fn configuration(id: u64, members: &BTreeMap<u64, SocketAddr>) -> ReplicaConfig {
    ReplicaConfig {
        schema: "roboguide.controller-replica/v0.1".into(),
        cluster_id: "offline-quorum-test".into(),
        replica_id: id,
        members: members.clone(),
        application_schema: "test/checkpoint-v1".into(),
        application_policy_digest: "same-explicit-policy".into(),
    }
}

/// Waits only within the test deadline for a currently majority-confirmed leader.
async fn leader(nodes: &[(ReplicaNode, ReplicaServer)]) -> usize {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            for (index, (node, _)) in nodes.iter().enumerate() {
                if let Some(epoch) = node.current_epoch()
                    && node.check_epoch(epoch).await.is_ok()
                {
                    return index;
                }
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .expect("three members elect a live majority leader")
}

/// Actual majority durability survives leader loss/rejoin; minority and stale issuing epochs cannot accept.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn three_members_quorum_loss_crash_rejoin_and_epoch_fencing() {
    let directory = tempfile::tempdir().unwrap();
    let endpoints = members();
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &endpoints),
                &directory.path().join(format!("{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let index = leader(&nodes).await;
    let old_epoch = nodes[index].0.current_epoch().unwrap();
    let first = batch(None, 1);
    nodes[index]
        .0
        .commit(old_epoch, first.clone())
        .await
        .unwrap();
    for (other, (node, _)) in nodes.iter().enumerate() {
        if other != index {
            node.network_online.store(false, Ordering::SeqCst);
        }
    }
    assert!(
        nodes[index].0.check_epoch(old_epoch).await.is_err(),
        "minority cannot grant delivery"
    );
    assert!(
        nodes[index]
            .0
            .commit(old_epoch, batch(Some(&first.checkpoint), 2))
            .await
            .is_err(),
        "minority cannot accept"
    );
    for (node, _) in &nodes {
        node.network_online.store(true, Ordering::SeqCst);
    }
    let (old_node, old_server) = nodes.remove(index);
    old_node.shutdown().await.unwrap();
    drop(old_server);
    let next = leader(&nodes).await;
    let epoch = nodes[next].0.current_epoch().unwrap();
    assert!(epoch.term > old_epoch.term);
    assert!(nodes[next].0.check_epoch(old_epoch).await.is_err());
    assert_eq!(
        nodes[next].0.accepted_view().unwrap().checkpoint,
        Some(first.checkpoint.clone())
    );
    let second = batch(Some(&first.checkpoint), 2);
    nodes[next].0.commit(epoch, second.clone()).await.unwrap();
    let previous_id = old_epoch.replica_id;
    drop(old_node);
    nodes.push(
        ReplicaNode::start(
            configuration(previous_id, &endpoints),
            &directory.path().join(format!("{previous_id}.sqlite3")),
        )
        .await
        .unwrap(),
    );
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            if nodes.iter().all(|(node, _)| {
                node.accepted_view().unwrap().checkpoint == Some(second.checkpoint.clone())
            }) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .unwrap();
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}

/// Schema/member/network mismatch cannot become a consensus participant.
#[test]
fn configuration_rejects_unsupported_network_and_membership() {
    let mut config = configuration(1, &members());
    config.validate().unwrap();
    config.members.insert(1, "0.0.0.0:10001".parse().unwrap());
    assert!(config.validate().is_err());
    config = configuration(1, &members());
    config.members.remove(&3);
    assert!(config.validate().is_err());
}

/// Persistence failure rolls back history and consensus apply; corrupted snapshots/rows cannot become authority.
#[tokio::test]
async fn sql_failure_and_corrupt_accepted_evidence_fail_closed() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("replica.sqlite3");
    let mut store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch(
        "CREATE TRIGGER fail_checkpoint BEFORE INSERT ON replica_meta
        WHEN NEW.key='checkpoint' BEGIN SELECT RAISE(ABORT,'injected checkpoint failure'); END;",
    )
    .unwrap();
    let entry = Entry {
        log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
        payload: EntryPayload::Normal(batch(None, 1)),
    };
    assert!(store.apply([entry.clone()]).await.is_err());
    assert_eq!(store.accepted_view().unwrap().revision, 0);
    assert!(store.applied_state().await.unwrap().0.is_none());
    sql.execute_batch("DROP TRIGGER fail_checkpoint").unwrap();
    store.apply([entry]).await.unwrap();
    let snapshot = store.build_snapshot().await.unwrap();
    let mut data = snapshot.snapshot.into_inner();
    let position = data
        .windows(11)
        .position(|bytes| bytes == b"received_at")
        .unwrap();
    data[position] = b'R';
    let mut target = SqliteRaftStore::open(
        &directory.path().join("target.sqlite3"),
        "identity",
        "test/checkpoint-v1",
    )
    .unwrap();
    assert!(
        target
            .install_snapshot(&snapshot.meta, Box::new(Cursor::new(data)))
            .await
            .is_err()
    );
    assert_eq!(target.accepted_view().unwrap().revision, 0);
    sql.execute(
        "UPDATE replica_events SET data=replace(data,'timestamp_ms\":3','timestamp_ms\":4')",
        [],
    )
    .unwrap();
    assert!(store.accepted_view().is_err());
    drop(store);
    assert!(SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").is_err());
}
