//! Upstream storage conformance plus actual three-member loopback quorum/failure checks.

use super::*;
use openraft::storage::RaftStateMachine;
use openraft::{CommittedLeaderId, Entry, EntryPayload, LogId, RaftSnapshotBuilder};
use sha2::Digest as _;
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
pub(crate) fn batch(
    previous: Option<&state::PersistedCheckpoint>,
    revision: u64,
) -> state::ApplicationCommitBatch {
    let tail = previous.map_or(0, |checkpoint| checkpoint.event_sequence);
    let mut batch = state::ApplicationCommitBatch {
        schema: state::APPLICATION_COMMIT_SCHEMA.into(),
        previous_event_sequence: tail,
        previous_checkpoint_digest: previous.map(state::checkpoint_identity),
        checkpoint: state::PersistedCheckpoint { schema:"test/checkpoint-v1".into(), event_sequence:tail + 1,
            checkpoint_json:serde_json::json!({"revision":revision,"unknown":"retained","ownership":"held","received_at":3}).to_string() },
        events:vec![state::PersistedEvent { sequence:tail + 1, event_id:format!("event-{}", tail + 1), timestamp_ms:3,
            correlation_id:"mission".into(),causation_id:None,payload_schema:"domain.EventPayload.json/v16".into(),
            payload_json:serde_json::json!({"MissionSchedulingPriorityChanged":{"mission_id":"mission","priority":17}}).to_string() }],
        commands: Vec::new(),
    };
    batch.commands.push(state::ControlCommandJournalEntry {
        command_id: format!("command-{revision}"),
        causation_id: None,
        authority_revision: revision,
        controller_epoch: Some("test".into()),
        plan_digest: None,
        input_digest: format!("input-{revision}"),
        ownership_digest: None,
        resulting_state_digest: format!(
            "sha256:{:x}",
            sha2::Sha256::digest(batch.checkpoint.checkpoint_json.as_bytes())
        ),
        checkpoint_event_sequence: batch.checkpoint.event_sequence,
        checkpoint_schema: batch.checkpoint.schema.clone(),
    });
    batch
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
            payload: EntryPayload::Normal(first.clone().into()),
        }])
        .await
        .unwrap();
    assert!(accepted[0].accepted);
    let rejected = store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 2),
            payload: EntryPayload::Normal(first.clone().into()),
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

/// The small accepted identity is sufficient to resolve a lost application Commit response.
#[tokio::test]
async fn accepted_application_identity_matches_checkpoint_digest() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("identity.sqlite3");
    let mut store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let first = batch(None, 1);
    store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
            payload: EntryPayload::Normal(first.clone().into()),
        }])
        .await
        .unwrap();
    let identity = store.accepted_application_identity().unwrap();
    assert_eq!(identity.revision, 1);
    assert_eq!(identity.event_sequence, first.checkpoint.event_sequence);
    assert_eq!(
        identity.checkpoint_digest,
        Some(state::checkpoint_identity(&first.checkpoint))
    );
}

/// Explicit history compaction retains a complete checkpoint and a restart-verifiable prefix anchor.
#[tokio::test]
async fn compacted_history_reopens_and_materializes_from_checkpoint() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("compact.sqlite3");
    let mut store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let first = batch(None, 1);
    store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
            payload: EntryPayload::Normal(first.clone().into()),
        }])
        .await
        .unwrap();
    let anchor = store.compact_history(0).unwrap();
    assert_eq!(anchor.base_sequence, first.checkpoint.event_sequence);
    let view = store.accepted_view().unwrap();
    assert!(view.events.is_empty());
    assert_eq!(view.history_anchor, Some(anchor));
    let materialized =
        state::SqliteEventLog::open(directory.path().join("materialized.sqlite3")).unwrap();
    materialized
        .install_replica_view_with_history_floor(
            &view.events,
            view.checkpoint.as_ref().unwrap(),
            view.history_anchor
                .as_ref()
                .map(|value| value.base_sequence),
        )
        .unwrap();
    assert_eq!(
        materialized.latest_sequence().unwrap(),
        first.checkpoint.event_sequence
    );
    drop(store);
    let reopened = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    assert_eq!(
        reopened.accepted_view().unwrap().checkpoint,
        Some(first.checkpoint)
    );
}

/// An actual child descriptor cannot outlive the final application writer lease or create a second owner.
#[test]
fn final_store_releases_lease_even_when_a_child_inherits_its_descriptor() {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("writer.sqlite3");
    let store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let retained = store.clone();
    let mut child = Command::new("sh")
        .args(["-c", "read token"])
        .stdin(Stdio::piped())
        .stdout(Stdio::from(store.writer_descriptor_for_test().unwrap()))
        .spawn()
        .unwrap();
    drop(store);
    assert!(SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").is_err());
    drop(retained);
    let reopened = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1");
    child.stdin.take().unwrap().write_all(b"done\n").unwrap();
    assert!(child.wait().unwrap().success());
    assert!(reopened.is_ok());
}

/// Database symlink aliases share the same process lease and cannot fork consensus history.
#[cfg(unix)]
#[test]
fn replica_database_alias_cannot_acquire_a_second_writer() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("writer.sqlite3");
    let alias = directory.path().join("alias.sqlite3");
    let store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    std::os::unix::fs::symlink(&path, &alias).unwrap();
    assert!(SqliteRaftStore::open(&alias, "identity", "test/checkpoint-v1").is_err());
    drop(store);
    assert!(SqliteRaftStore::open(&alias, "identity", "test/checkpoint-v1").is_ok());
}

/// Database hard links cannot bypass the actual SQLite inode writer lease.
#[cfg(unix)]
#[test]
fn replica_database_hard_link_cannot_acquire_a_second_writer() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("writer.sqlite3");
    let alias = directory.path().join("hardlink.sqlite3");
    let store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    std::fs::hard_link(&path, &alias).unwrap();
    assert!(
        SqliteRaftStore::open(&alias, "identity", "test/checkpoint-v1").is_err(),
        "an alias of the same database inode must not create another authority"
    );
    drop(store);
    assert!(SqliteRaftStore::open(&alias, "identity", "test/checkpoint-v1").is_ok());
}

/// A handoff intent survives a Store restart and cannot regress or overlap an uncertain request.
#[test]
fn durable_leader_transfer_record_is_monotonic_and_restart_safe() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("transfer.sqlite3");
    let store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let record = LeaderTransferRecord {
        transfer_id: "durable-transfer".into(),
        source_epoch: ControllerEpoch {
            term: 3,
            replica_id: 1,
        },
        target: 2,
        required_last_log_index: Some(9),
        state: "prepared".into(),
        confirmed_epoch: None,
        source_process: None,
        source_drain: None,
    };
    store.persist_leader_transfer(&record).unwrap();
    assert_eq!(
        store.leader_transfer_record().unwrap(),
        Some(record.clone())
    );
    drop(store);
    let reopened = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    assert_eq!(
        reopened.leader_transfer_record().unwrap(),
        Some(record.clone())
    );
    assert!(
        reopened
            .persist_leader_transfer(&LeaderTransferRecord {
                state: "prepared".into(),
                source_epoch: ControllerEpoch {
                    term: 2,
                    replica_id: 1,
                },
                ..record.clone()
            })
            .is_err()
    );
    assert!(
        reopened
            .persist_leader_transfer(&LeaderTransferRecord {
                transfer_id: "other-transfer".into(),
                state: "prepared".into(),
                ..record
            })
            .is_err()
    );
}

/// The first new writer migrates every complete legacy table and fences old table-name writers.
#[test]
fn legacy_replica_storage_migrates_atomically_and_old_writes_are_refused() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy.sqlite3");
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute_batch(
            "CREATE TABLE replica_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
             CREATE TABLE replica_commands(revision INTEGER PRIMARY KEY,command_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL);
             CREATE TABLE replica_log(log_index INTEGER PRIMARY KEY,data TEXT NOT NULL);
             CREATE TABLE replica_events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL);
             CREATE TABLE replica_snapshot(singleton INTEGER PRIMARY KEY CHECK(singleton=1),meta TEXT NOT NULL,data BLOB NOT NULL);
             INSERT INTO replica_meta(key,value) VALUES ('identity','[\"identity\",\"test/checkpoint-v1\"]');",
        )
        .unwrap();
    drop(connection);
    let store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    assert_eq!(store.accepted_view().unwrap().revision, 0);
    let connection = rusqlite::Connection::open(&path).unwrap();
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        3
    );
    for name in [
        "replica_meta",
        "replica_commands",
        "replica_log",
        "replica_events",
        "replica_snapshot",
    ] {
        assert_eq!(
            connection
                .query_row(
                    "SELECT type FROM sqlite_master WHERE name=?1",
                    [name],
                    |row| row.get::<_, String>(0),
                )
                .unwrap(),
            "view"
        );
    }
    assert!(
        connection
            .execute(
                "INSERT INTO replica_meta(key,value) VALUES ('legacy-write','must-refuse')",
                [],
            )
            .is_err()
    );
    drop(connection);
    drop(store);
}

/// The main-file migration fence requires every legacy WAL connection to relinquish SQLite locks.
#[test]
fn live_legacy_sqlite_connection_blocks_replica_format_migration() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy-live.sqlite3");
    let legacy = rusqlite::Connection::open(&path).unwrap();
    legacy
        .execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE replica_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
             CREATE TABLE replica_commands(revision INTEGER PRIMARY KEY,command_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL);
             CREATE TABLE replica_log(log_index INTEGER PRIMARY KEY,data TEXT NOT NULL);
             CREATE TABLE replica_events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL);
             CREATE TABLE replica_snapshot(singleton INTEGER PRIMARY KEY CHECK(singleton=1),meta TEXT NOT NULL,data BLOB NOT NULL);
             INSERT INTO replica_meta VALUES ('identity','[\"identity\",\"test/checkpoint-v1\"]');",
        )
        .unwrap();
    let refused = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1");
    assert!(refused.err().unwrap().contains("not quiescent"));
    assert_eq!(
        legacy
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        0
    );
    drop(legacy);
    assert!(SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").is_ok());
}

/// Wrong original identity and altered view fences refuse before modifying retained authority rows.
#[test]
fn replica_format_reopen_rejects_altered_readonly_view() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("altered.sqlite3");
    drop(SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap());
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch(
        "DROP VIEW replica_meta;
         CREATE VIEW replica_meta AS SELECT * FROM replica_v1_log;",
    )
    .unwrap();
    drop(sql);
    assert!(
        SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1")
            .err()
            .unwrap()
            .contains("readonly view differs")
    );
}

/// Missing identity remains unknown; reopening cannot silently invent a new cluster authority.
#[test]
fn replica_format_requires_original_identity_before_any_repair() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("missing-identity.sqlite3");
    drop(SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap());
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute("DELETE FROM replica_v1_meta WHERE key='identity'", [])
        .unwrap();
    drop(connection);
    let before = std::fs::read(&path).unwrap();
    assert!(
        SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1")
            .err()
            .unwrap()
            .contains("identity is absent or differs")
    );
    assert_eq!(std::fs::read(path).unwrap(), before);
}

/// Acquires three simultaneous ephemeral listeners so their released loopback ports are distinct.
fn members() -> BTreeMap<u64, SocketAddr> {
    members_with_count(3)
}

/// Acquires a pre-enrolled member universe for dynamic membership tests.
fn members_with_count(count: usize) -> BTreeMap<u64, SocketAddr> {
    let listeners = (0..count)
        .map(|_| std::net::TcpListener::bind("127.0.0.1:0").unwrap())
        .collect::<Vec<_>>();
    listeners
        .iter()
        .enumerate()
        .map(|(index, listener)| (index as u64 + 1, listener.local_addr().unwrap()))
        .collect()
}

/// Supplies a v0.2 profile whose initial voters can be smaller than its pre-enrolled universe.
fn dynamic_configuration(
    id: u64,
    members: &BTreeMap<u64, SocketAddr>,
    initial_voters: BTreeSet<u64>,
) -> ReplicaConfig {
    ReplicaConfig {
        schema: "roboguide.controller-replica/v0.2".into(),
        cluster_id: "offline-dynamic-membership-test".into(),
        replica_id: id,
        members: members.clone(),
        initial_voters: Some(initial_voters),
        application_schema: "test/checkpoint-v1".into(),
        application_policy_digest: "same-explicit-policy".into(),
        shared_secret: None,
        tls: None,
    }
}

/// Supplies one exact homogeneous policy for isolated real replica engines.
fn configuration(id: u64, members: &BTreeMap<u64, SocketAddr>) -> ReplicaConfig {
    ReplicaConfig {
        schema: "roboguide.controller-replica/v0.1".into(),
        cluster_id: "offline-quorum-test".into(),
        replica_id: id,
        members: members.clone(),
        initial_voters: None,
        application_schema: "test/checkpoint-v1".into(),
        application_policy_digest: "same-explicit-policy".into(),
        shared_secret: None,
        tls: None,
    }
}

/// Waits only within the test deadline for a currently majority-confirmed leader.
pub(crate) async fn leader(nodes: &[(ReplicaNode, ReplicaServer)]) -> usize {
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

/// A target-side election request moves leadership without replaying or forcing an application write.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn controlled_leader_transfer_moves_to_caught_up_voter() {
    let directory = tempfile::tempdir().unwrap();
    let endpoints = members();
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &endpoints),
                &directory.path().join(format!("replica-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let leader_index = leader(&nodes).await;
    let old_epoch = nodes[leader_index].0.current_epoch().unwrap();
    let target = (1..=3).find(|id| *id != old_epoch.replica_id).unwrap();
    // Majority leadership does not imply this particular target has acknowledged the current tail.
    // Wait for the actual replication observation before the one original effect request.
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let metrics = nodes[leader_index].0.raft.metrics().borrow().clone();
            if nodes[leader_index].0.current_epoch() == Some(old_epoch)
                && metrics
                    .replication
                    .as_ref()
                    .and_then(|members| members.get(&target))
                    .is_some_and(|position| position.map(|log| log.index) >= metrics.last_log_index)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the exact selected target acknowledges the current original leader log");
    let receipt = nodes[leader_index]
        .0
        .request_leader_transfer(target, "transfer-test-1")
        .await
        .unwrap();
    assert_eq!(receipt.state, "requested");
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            if let Some(epoch) = nodes[(target - 1) as usize].0.current_epoch()
                && epoch.replica_id == target
                && epoch.term > old_epoch.term
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .expect("target voter becomes leader after explicit transfer");
    assert_ne!(nodes[leader_index].0.current_epoch(), Some(old_epoch));
    for (node, _) in nodes {
        node.shutdown().await.unwrap();
    }
}

/// Waits for the actual selected pair before an original transfer, without retrying its mutation.
async fn selected_transfer_target(source: &ReplicaNode, target: u64) {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let metrics = source.raft.metrics().borrow().clone();
            if source.current_epoch().is_some()
                && metrics
                    .replication
                    .as_ref()
                    .and_then(|members| members.get(&target))
                    .is_some_and(|position| position.map(|log| log.index) >= metrics.last_log_index)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("the original source and selected target have actual current replication evidence");
}

/// A former source must serve idle heartbeats again after leadership explicitly returns to it.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn repeated_transfer_back_to_original_source_preserves_idle_quorum() {
    let directory = tempfile::tempdir().unwrap();
    let members = members_with_count(3);
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &members),
                &directory.path().join(format!("member-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let source = leader(&nodes).await;
    let target = (source + 1) % nodes.len();
    selected_transfer_target(&nodes[source].0, nodes[target].0.config.replica_id).await;
    nodes[source]
        .0
        .request_leader_transfer(nodes[target].0.config.replica_id, "round-trip-first")
        .await
        .unwrap();
    selected_transfer_target(&nodes[target].0, nodes[source].0.config.replica_id).await;
    nodes[target]
        .0
        .request_leader_transfer(
            nodes[source].0.config.replica_id,
            "round-trip-original-owner",
        )
        .await
        .unwrap();
    let expected = nodes[source].0.current_epoch().unwrap();
    // No application writes or linearizable probes may mask the missing idle heartbeat.
    tokio::time::sleep(Duration::from_secs(4)).await;
    assert_eq!(
        nodes[source].0.current_epoch(),
        Some(expected),
        "former source did not restore its automatic idle heartbeats"
    );
    nodes[source].0.check_epoch(expected).await.unwrap();
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}

/// Cancelling a request waiting at the actual target writer cannot leave its source ticker disabled.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn cancelled_actual_transfer_wait_restores_original_automatic_heartbeat() {
    let directory = tempfile::tempdir().unwrap();
    let members = members_with_count(3);
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &members),
                &directory.path().join(format!("member-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let source = leader(&nodes).await;
    let target = (source + 1) % nodes.len();
    let expected = nodes[source].0.current_epoch().unwrap();
    let target_writer = nodes[target].0.member_writer.lock().await;
    let before = nodes[target]
        .0
        .framed_member_mutations
        .load(Ordering::SeqCst);
    let target_id = nodes[target].0.config.replica_id;
    let original_source = nodes[source].0.clone();
    let request = tokio::spawn(async move {
        original_source
            .request_leader_transfer(target_id, "cancelled-target-wait")
            .await
    });
    tokio::time::timeout(Duration::from_secs(5), async {
        while nodes[target]
            .0
            .framed_member_mutations
            .load(Ordering::SeqCst)
            == before
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("actual transfer frame reached the original held target writer");
    request.abort();
    assert!(request.await.unwrap_err().is_cancelled());
    // Cover both the maximum election timeout and the retained follower leader lease.
    tokio::time::sleep(Duration::from_secs(8)).await;
    assert_eq!(
        nodes[source].0.current_epoch(),
        Some(expected),
        "cancelled source left election or heartbeat disabled"
    );
    drop(target_writer);
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}

/// A pre-enrolled learner catches up before joint consensus admits and later removes its vote.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dynamic_membership_uses_learner_catchup_and_joint_consensus() {
    let directory = tempfile::tempdir().unwrap();
    let endpoints = members_with_count(4);
    let initial = [1_u64, 2, 3].into_iter().collect::<BTreeSet<_>>();
    let mut nodes = Vec::new();
    for id in 1..=4 {
        nodes.push(
            ReplicaNode::start(
                dynamic_configuration(id, &endpoints, initial.clone()),
                &directory.path().join(format!("replica-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let leader_index = leader(&nodes[..3]).await;
    nodes[leader_index].0.add_learner(4).await.unwrap();
    nodes[leader_index]
        .0
        .change_membership([1_u64, 2, 3, 4].into_iter().collect(), true)
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let applied = nodes[3]
                .0
                .raft
                .metrics()
                .borrow()
                .membership_config
                .voter_ids()
                .collect::<BTreeSet<_>>();
            if applied == [1_u64, 2, 3, 4].into_iter().collect() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .expect("new voter independently applies the committed complete membership");
    let current_leader = leader(&nodes).await;
    nodes[current_leader]
        .0
        .change_membership([1_u64, 2, 3].into_iter().collect(), true)
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if !nodes[3]
                .0
                .raft
                .metrics()
                .borrow()
                .membership_config
                .voter_ids()
                .any(|id| id == 4)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .expect("removed member loses voter status after joint consensus");
    for (node, _) in nodes {
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

/// Replica transport authentication accepts only a bounded secret profile.
/// One member cannot reuse another member's leaf pin or configure a noncanonical digest.
#[test]
fn tls_configuration_requires_distinct_canonical_member_pins() {
    let mut config = configuration(1, &members());
    config.tls = Some(ReplicaTlsConfig {
        ca_cert_path: "/fixture/ca.crt".into(),
        cert_path: "/fixture/1.crt".into(),
        key_path: "/fixture/1.key".into(),
        server_name: "localhost".into(),
        peer_certificate_digests: (1..=3)
            .map(|member| {
                (
                    member,
                    format!("sha256:{}", format!("{member:x}").repeat(64)),
                )
            })
            .collect(),
    });
    config.validate().unwrap();
    let tls = config.tls.as_mut().unwrap();
    tls.peer_certificate_digests
        .insert(2, tls.peer_certificate_digests[&1].clone());
    assert!(config.validate().is_err());
    config
        .tls
        .as_mut()
        .unwrap()
        .peer_certificate_digests
        .insert(2, format!("sha256:{}", "G".repeat(64)));
    assert!(config.validate().is_err());
}

/// Replica transport authentication accepts only a bounded secret profile.
#[test]
fn configuration_rejects_short_transport_secret() {
    let mut config = configuration(1, &members());
    config.shared_secret = Some("too-short".into());
    assert!(config.validate().is_err());
    config.shared_secret = Some("s".repeat(32));
    assert!(config.validate().is_ok());
    assert_ne!(
        config.profile_identity(),
        configuration(1, &config.members).profile_identity(),
        "security profile participates in the immutable cluster identity"
    );
}

/// Persistence failure rolls back history and consensus apply; corrupted snapshots/rows cannot become authority.
#[tokio::test]
async fn sql_failure_and_corrupt_accepted_evidence_fail_closed() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("replica.sqlite3");
    let mut store = SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").unwrap();
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch(
        "CREATE TRIGGER fail_checkpoint BEFORE INSERT ON replica_v1_meta
        WHEN NEW.key='checkpoint' BEGIN SELECT RAISE(ABORT,'injected checkpoint failure'); END;",
    )
    .unwrap();
    let entry = Entry {
        log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
        payload: EntryPayload::Normal(batch(None, 1).into()),
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
            .install_snapshot(&snapshot.meta, Box::new(ReplicaSnapshotData::new(data)))
            .await
            .is_err()
    );
    assert_eq!(target.accepted_view().unwrap().revision, 0);
    sql.execute(
        "UPDATE replica_v1_events SET data=replace(data,'timestamp_ms\":3','timestamp_ms\":4')",
        [],
    )
    .unwrap();
    assert!(store.accepted_view().is_err());
    drop(store);
    assert!(SqliteRaftStore::open(&path, "identity", "test/checkpoint-v1").is_err());
}

/// Raft snapshots neither transplant a sender's hold nor erase a receiver's independent operation.
#[tokio::test]
async fn raft_snapshot_preserves_member_local_transfer_history() {
    let directory = tempfile::tempdir().unwrap();
    let mut source = SqliteRaftStore::open(
        &directory.path().join("source.sqlite3"),
        "same",
        "test/checkpoint-v1",
    )
    .unwrap();
    let mut target = SqliteRaftStore::open(
        &directory.path().join("target.sqlite3"),
        "same",
        "test/checkpoint-v1",
    )
    .unwrap();
    let mut unrelated = SqliteRaftStore::open(
        &directory.path().join("unrelated.sqlite3"),
        "same",
        "test/checkpoint-v1",
    )
    .unwrap();
    let record = LeaderTransferRecord {
        transfer_id: "source-only".into(),
        source_epoch: ControllerEpoch {
            term: 1,
            replica_id: 1,
        },
        target: 2,
        required_last_log_index: None,
        state: "prepared".into(),
        confirmed_epoch: None,
        source_process: None,
        source_drain: None,
    };
    source.persist_leader_transfer(&record).unwrap();
    let own = LeaderTransferRecord {
        transfer_id: "target-only".into(),
        target: 3,
        ..record.clone()
    };
    target.persist_leader_transfer(&own).unwrap();
    let snapshot = source.build_snapshot().await.unwrap();
    let bytes = snapshot.snapshot.into_inner();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert!(body.get("leader_transfer").is_none());
    target
        .install_snapshot(
            &snapshot.meta,
            Box::new(ReplicaSnapshotData::new(bytes.clone())),
        )
        .await
        .unwrap();
    unrelated
        .install_snapshot(&snapshot.meta, Box::new(ReplicaSnapshotData::new(bytes)))
        .await
        .unwrap();
    assert_eq!(target.leader_transfer_record().unwrap(), Some(own));
    assert_eq!(unrelated.leader_transfer_record().unwrap(), None);
}

/// Exact original identities remain retrievable and an uncertain or completed result cannot flip.
#[test]
fn transfer_history_retains_original_operations_and_immutable_completion() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("history.sqlite3");
    let store = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    let unknown = LeaderTransferRecord {
        transfer_id: "first".into(),
        source_epoch: ControllerEpoch {
            term: 1,
            replica_id: 1,
        },
        target: 2,
        required_last_log_index: Some(5),
        state: "unknown".into(),
        confirmed_epoch: None,
        source_process: None,
        source_drain: None,
    };
    store.persist_leader_transfer(&unknown).unwrap();
    assert!(
        store
            .persist_leader_transfer(&LeaderTransferRecord {
                state: "rejected".into(),
                ..unknown.clone()
            })
            .is_err()
    );
    assert!(
        store
            .persist_leader_transfer(&LeaderTransferRecord {
                state: "completed".into(),
                ..unknown.clone()
            })
            .is_err()
    );
    let completed = LeaderTransferRecord {
        state: "completed".into(),
        source_drain: Some(ControllerDrainProof::NoApplicationProcess {
            supervisor_instance: "00000000-0000-0000-0000-000000000001".into(),
            epoch: unknown.source_epoch,
        }),
        confirmed_epoch: Some(ControllerEpoch {
            term: 2,
            replica_id: 2,
        }),
        ..unknown
    };
    store.persist_leader_transfer(&completed).unwrap();
    assert!(
        store
            .persist_leader_transfer(&LeaderTransferRecord {
                state: "unknown".into(),
                confirmed_epoch: None,
                ..completed.clone()
            })
            .is_err()
    );
    let next = LeaderTransferRecord {
        transfer_id: "second".into(),
        state: "prepared".into(),
        confirmed_epoch: None,
        ..completed.clone()
    };
    store.persist_leader_transfer(&next).unwrap();
    drop(store);
    let reopened = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    assert_eq!(
        reopened.leader_transfer_by_id("first").unwrap(),
        Some(completed)
    );
    assert_eq!(reopened.leader_transfer_record().unwrap(), Some(next));
}

/// A real dropped TCP response after target election stays Unknown and reconciles without resend.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn lost_target_transfer_reply_reconciles_original_operation_without_repeat() {
    let directory = tempfile::tempdir().unwrap();
    let endpoints = members();
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &endpoints),
                &directory.path().join(format!("replica-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let source = leader(&nodes).await;
    let source_epoch = nodes[source].0.current_epoch().unwrap();
    let target = (source + 1) % nodes.len();
    let target_id = nodes[target].0.config.replica_id;
    nodes[target]
        .0
        .discard_transfer_reply
        .store(true, Ordering::SeqCst);
    assert!(
        nodes[source]
            .0
            .request_leader_transfer(target_id, "lost-original-reply")
            .await
            .is_err()
    );
    assert_eq!(
        nodes[source]
            .0
            .leader_transfer_record()
            .unwrap()
            .unwrap()
            .state,
        "unknown"
    );
    assert!(nodes[source].0.check_epoch(source_epoch).await.is_err());
    let before = nodes[target]
        .0
        .framed_member_mutations
        .load(Ordering::SeqCst);
    let receipt = nodes[source]
        .0
        .request_leader_transfer(target_id, "lost-original-reply")
        .await
        .unwrap();
    assert_eq!(receipt.state, "unknown");
    assert_eq!(
        nodes[target]
            .0
            .framed_member_mutations
            .load(Ordering::SeqCst),
        before
    );
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            if nodes[target].0.reconcile_leader_transfer().await.unwrap()
                && nodes[source].0.reconcile_leader_transfer().await.unwrap()
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(30)).await;
        }
    })
    .await
    .expect("original target quorum proof reconciles dropped reply");
    assert_eq!(
        nodes[source]
            .0
            .leader_transfer_record()
            .unwrap()
            .unwrap()
            .state,
        "completed"
    );
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}

/// Legacy weak completion remains original history and cannot silently clear a v2 source hold.
#[test]
fn v1_transfer_upgrade_preserves_raw_evidence_and_fences_old_completion() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy-v1.sqlite3");
    drop(SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap());
    let original = serde_json::json!({
        "transfer_id": "legacy", "source_epoch": {"term": 1, "replica_id": 1},
        "target": 2, "required_last_log_index": 5, "state": "completed"
    })
    .to_string();
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection.execute_batch(
        "DROP TABLE replica_storage_format;
         CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version=1),schema TEXT NOT NULL);
         INSERT INTO replica_storage_format VALUES (1,'roboguide.replica-storage/v0.1');
         DELETE FROM replica_v1_meta WHERE key='leader_transfers_v2';
         PRAGMA user_version=1;"
    ).unwrap();
    connection
        .execute(
            "INSERT INTO replica_v1_meta VALUES ('leader_transfer',?1)",
            [&original],
        )
        .unwrap();
    drop(connection);
    let store = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    let upgraded = store.leader_transfer_record().unwrap().unwrap();
    assert_eq!(upgraded.transfer_id, "legacy");
    assert_eq!(upgraded.state, "unknown");
    assert_eq!(upgraded.confirmed_epoch, None);
    drop(store);
    let connection = rusqlite::Connection::open(&path).unwrap();
    assert_eq!(
        connection
            .query_row(
                "SELECT value FROM replica_v1_meta WHERE key='leader_transfer'",
                [],
                |row| row.get::<_, String>(0)
            )
            .unwrap(),
        original
    );
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |row| row.get::<_, u64>(0))
            .unwrap(),
        3
    );
}

/// Owns one real software child so any assertion failure still kills and reaps that exact process.
struct OriginalChild(std::process::Child);

impl Drop for OriginalChild {
    /// Reaps only the original handle, never searching for another process or device.
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// Target election cannot precede the source supervisor's durable original Child.wait evidence.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn original_controller_child_wait_precedes_target_transfer_acceptance() {
    use std::os::unix::process::ExitStatusExt;
    let directory = tempfile::tempdir().unwrap();
    let endpoints = members();
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &endpoints),
                &directory.path().join(format!("replica-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let source = leader(&nodes).await;
    let epoch = nodes[source].0.current_epoch().unwrap();
    nodes[source].0.enable_controller_supervision().unwrap();
    let instance = nodes[source]
        .0
        .begin_controller_process(epoch)
        .await
        .unwrap();
    let mut child = OriginalChild(
        std::process::Command::new("/bin/sleep")
            .arg("60")
            .spawn()
            .unwrap(),
    );
    let identity = nodes[source]
        .0
        .register_controller_process(epoch, instance, child.0.id())
        .unwrap();
    let target = (source + 1) % nodes.len();
    let target_id = nodes[target].0.config.replica_id;
    let before = nodes[target]
        .0
        .framed_member_mutations
        .load(Ordering::SeqCst);
    let owned_source = nodes[source].0.clone();
    let transfer = tokio::spawn(async move {
        owned_source
            .request_leader_transfer(target_id, "original-owned-child")
            .await
    });
    tokio::time::timeout(Duration::from_secs(2), async {
        while nodes[source].0.leader_transfer_record().unwrap().is_none() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    assert!(nodes[source].0.check_epoch(epoch).await.is_err());
    assert_eq!(
        nodes[target]
            .0
            .framed_member_mutations
            .load(Ordering::SeqCst),
        before
    );
    child.0.kill().unwrap();
    let status = child.0.wait().unwrap();
    // Even an actual OS wait outside the authority journal cannot authorize the target election.
    tokio::time::sleep(Duration::from_millis(60)).await;
    assert_eq!(
        nodes[target]
            .0
            .framed_member_mutations
            .load(Ordering::SeqCst),
        before
    );
    nodes[source]
        .0
        .record_controller_exit(ControllerProcessExit {
            process: identity.clone(),
            exit_code: status.code(),
            signal: status.signal(),
        })
        .unwrap();
    transfer.await.unwrap().unwrap();
    let admitted = nodes[target].0.leader_transfer_record().unwrap().unwrap();
    assert_eq!(admitted.source_process, Some(identity.clone()));
    assert!(
        matches!(admitted.source_drain, Some(ControllerDrainProof::ProcessExit { outcome }) if outcome.process == identity)
    );
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}

/// A restarted supervisor cannot invent a wait result or start another process over an uncertain PID.
#[tokio::test]
async fn controller_process_restart_keeps_original_unreaped_capacity_fence() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("original.sqlite3");
    let store = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    store.enable_controller_supervision().unwrap();
    let original_supervisor = supervision::new_process_instance().unwrap();
    let epoch = ControllerEpoch {
        term: 1,
        replica_id: 1,
    };
    let original = store
        .begin_controller_process(epoch, &original_supervisor)
        .unwrap();
    drop(store);
    let restored = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    assert!(
        restored
            .begin_controller_process(epoch, &supervision::new_process_instance().unwrap())
            .is_err()
    );
    assert_eq!(
        restored
            .controller_drain_proof(epoch, None, &original_supervisor)
            .unwrap(),
        None
    );
    assert!(restored.transfer_source_process(epoch).is_err());
    let sql = rusqlite::Connection::open(&path).unwrap();
    let records: serde_json::Value = serde_json::from_str(
        &sql.query_row(
            "SELECT value FROM replica_v1_meta WHERE key='controller_processes_v1'",
            [],
            |row| row.get::<_, String>(0),
        )
        .unwrap(),
    )
    .unwrap();
    assert_eq!(records[0]["instance_id"], original);
}

/// Actual member-writer queue time consumes the original receiver budget before election admission.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn queued_target_transfer_cannot_renew_original_receive_budget() {
    let directory = tempfile::tempdir().unwrap();
    let endpoints = members();
    let mut nodes = Vec::new();
    for id in 1..=3 {
        nodes.push(
            ReplicaNode::start(
                configuration(id, &endpoints),
                &directory.path().join(format!("replica-{id}.sqlite3")),
            )
            .await
            .unwrap(),
        );
    }
    let source = leader(&nodes).await;
    let epoch = nodes[source].0.current_epoch().unwrap();
    let target = (source + 1) % nodes.len();
    let target_id = nodes[target].0.config.replica_id;
    let lock = nodes[target].0.member_writer.lock().await;
    let before = nodes[target]
        .0
        .framed_member_mutations
        .load(Ordering::SeqCst);
    let source_node = nodes[source].0.clone();
    let proof = ControllerDrainProof::NoApplicationProcess {
        supervisor_instance: source_node.process_instance.clone(),
        epoch,
    };
    let request = tokio::spawn(async move {
        transport::request_leader_transfer(
            &source_node,
            target_id,
            "expired-original-ingress".into(),
            epoch,
            None,
            proof,
            50,
        )
        .await
    });
    tokio::time::timeout(Duration::from_secs(2), async {
        while nodes[target]
            .0
            .framed_member_mutations
            .load(Ordering::SeqCst)
            == before
        {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    tokio::time::sleep(Duration::from_millis(100)).await;
    drop(lock);
    assert!(
        request
            .await
            .unwrap()
            .unwrap_err()
            .contains("receive budget expired")
    );
    assert_eq!(nodes[target].0.leader_transfer_record().unwrap(), None);
    for (node, _) in &nodes {
        node.shutdown().await.unwrap();
    }
}

/// Legacy application bytes without an original process journal never become an empty-source proof.
#[tokio::test]
async fn legacy_application_checkpoint_keeps_unknown_controller_continuity() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy-application.sqlite3");
    let store = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    let mut store = store;
    store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
            payload: EntryPayload::Normal(batch(None, 1).into()),
        }])
        .await
        .unwrap();
    drop(store);
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch("DROP TABLE replica_storage_format; CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version=2),schema TEXT NOT NULL); INSERT INTO replica_storage_format VALUES (2,'roboguide.replica-storage/v0.2'); PRAGMA user_version=2; DELETE FROM replica_v1_meta WHERE key IN ('controller_supervision_v1','controller_processes_v1','legacy_controller_continuity_unknown');").unwrap();
    drop(sql);
    let restored = SqliteRaftStore::open(&path, "same", "test/checkpoint-v1").unwrap();
    assert!(!restored.controller_continuity_known().unwrap());
    assert!(restored.enable_controller_supervision().is_err());
    assert!(
        restored
            .transfer_source_process(ControllerEpoch {
                term: 2,
                replica_id: 1
            })
            .is_err()
    );
    assert_eq!(restored.accepted_view().unwrap().revision, 1);
}
