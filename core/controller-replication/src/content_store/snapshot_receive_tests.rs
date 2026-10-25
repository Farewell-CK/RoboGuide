//! Real local SQL/CAS receive invariants; declared retention membership below is not deployed consensus.

use super::*;
use crate::{ControllerEpoch, ReplicaMember};
use openraft::{CommittedLeaderId, LogId, Membership, StoredMembership};
use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::time::Duration;

/// Creates actual owned files and roots with explicitly labelled synthetic source/receiver retention context.
fn fixture() -> (
    tempfile::TempDir,
    ReplicaContentStore,
    artifact_store::FileSystemArtifactStore,
    SnapshotContentHeader,
) {
    let directory = tempfile::tempdir().unwrap();
    let source = artifact_store::FileSystemArtifactStore::new_with_archive_references(
        directory.path().join("source"),
    )
    .unwrap();
    let bytes = b"actual original local Snapshot receipt bytes";
    let hash = artifact_store::digest_bytes(bytes);
    let mut upload = source.begin_upload("original-snapshot-fixture").unwrap();
    upload.write_chunk(bytes).unwrap();
    upload.finalize(&hash, bytes.len() as u64).unwrap();
    let profile = "a".repeat(64);
    let receiver = ReplicaContentStore::create_with_snapshot_receives(
        &directory.path().join("receiver"),
        &profile,
        17,
    )
    .unwrap();
    let members = BTreeMap::from([
        (
            17,
            ReplicaMember::with_content_store(
                "127.0.0.1:1",
                receiver.identity().store_owner.clone(),
            )
            .unwrap(),
        ),
        (
            23,
            ReplicaMember::with_content_store(
                "127.0.0.1:2",
                format!("replica-content:{}", "b".repeat(64)),
            )
            .unwrap(),
        ),
    ]);
    let log = LogId::new(CommittedLeaderId::new(3, 23), 8);
    let membership = StoredMembership::new(
        Some(log),
        Membership::new(vec![BTreeSet::from([23])], members),
    );
    let header = SnapshotContentHeader {
        schema: "roboguide.snapshot-content-header/v0.1".into(),
        source_member: 23,
        source_store_owner: format!("replica-content:{}", "b".repeat(64)),
        source_leaf: format!("sha256:{}", "c".repeat(64)),
        meta: SnapshotMeta {
            last_log_id: Some(log),
            last_membership: membership.clone(),
            snapshot_id: format!("8-1-{}", "d".repeat(64)),
        },
        payload_digest: "d".repeat(64),
        target: ContentQuorumTarget {
            profile_identity: profile,
            epoch: ControllerEpoch {
                term: 3,
                replica_id: 23,
            },
            revision: 1,
            checkpoint_digest: "e".repeat(64),
            batch_digest: "f".repeat(64),
            membership,
            member_stores: BTreeMap::from([(23, format!("replica-content:{}", "b".repeat(64)))]),
            inputs: vec![ArtifactContentKey {
                source_owner: source.owner_id().unwrap().into(),
                content_digest: hash,
                byte_size: bytes.len() as u64,
            }],
        },
    };
    (directory, receiver, source, header)
}

/// First durable intent precedes bytes; actual verification and separate parent publication gate retirement.
#[test]
fn original_snapshot_receive_verifies_bytes_and_releases_only_its_own_pins() {
    let (directory, root, source, header) = fixture();
    let key = &header.target.inputs[0];
    let permission: StreamPermission = Arc::new(|| Ok(()));
    let first = root
        .prepare_snapshot_receive(
            header.clone(),
            ContentBudget::issue(30000).unwrap(),
            permission.clone(),
        )
        .unwrap();
    assert!(first.ready.is_none());
    assert!(root.verify_content(key).is_err());
    assert!(
        root.complete_retained_snapshot_receive(&first.command_id, &header, permission.clone())
            .unwrap()
            .is_none()
    );
    assert!(
        root.complete_snapshot_receive(&first.command_id, permission.clone())
            .is_err()
    );
    assert_eq!(
        root.snapshot_receive_status(&first.command_id)
            .unwrap()
            .unwrap(),
        first
    );
    let copied = root
        .copy_from_artifact(
            &source,
            key,
            "original-snapshot-copy",
            &header.target.batch_digest,
            30000,
        )
        .unwrap();
    assert!(copied.receipt.is_some());
    let path = copy::object_path(root.root(), key);
    let bytes = std::fs::read(&path).unwrap();
    let original_mode = std::fs::metadata(&path).unwrap().permissions();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::write(&path, b"corrupt retained bytes cannot lend readiness").unwrap();
    assert!(
        root.complete_retained_snapshot_receive(&first.command_id, &header, permission.clone())
            .is_err()
    );
    assert_eq!(
        root.snapshot_receive_status(&first.command_id)
            .unwrap()
            .unwrap(),
        first
    );
    std::fs::write(&path, bytes).unwrap();
    std::fs::set_permissions(&path, original_mode).unwrap();
    let ready = root
        .complete_retained_snapshot_receive(&first.command_id, &header, permission.clone())
        .unwrap()
        .unwrap();
    assert!(ready.ready.is_some());
    assert!(ready.retirement.is_none());
    let mut changed = header.clone();
    changed.payload_digest = "0".repeat(64);
    assert!(
        root.require_snapshot_prerequisites(
            &changed.meta,
            &changed.payload_digest,
            1,
            &changed.target.checkpoint_digest,
            &changed.target.batch_digest,
            &changed.target.inputs
        )
        .is_err()
    );
    assert!(
        root.retire_snapshot_prerequisites(&first.command_id, &"1".repeat(64), || Err(
            "actual parent not published".into()
        ))
        .is_err()
    );
    assert_eq!(
        root.snapshot_receive_status(&first.command_id)
            .unwrap()
            .unwrap(),
        ready
    );
    root.retire_snapshot_prerequisites(&first.command_id, &"1".repeat(64), || Ok(()))
        .unwrap();
    let retired = root
        .snapshot_receive_status(&first.command_id)
        .unwrap()
        .unwrap();
    assert!(retired.retirement.is_some());
    assert_eq!(retired.ready, ready.ready);
    assert!(
        root.active_references(key)
            .unwrap()
            .iter()
            .all(|reference| reference.kind != ReplicaContentReferenceKind::SnapshotReceive)
    );
    root.verify_content(key).unwrap();
    let identity = root.identity().clone();
    drop(root);
    assert!(
        ReplicaContentStore::open_with_streams(
            &directory.path().join("receiver"),
            &identity.profile_identity,
            identity.member
        )
        .is_err()
    );
    let reopened = ReplicaContentStore::open_with_snapshot_receives(
        &directory.path().join("receiver"),
        &identity.profile_identity,
        identity.member,
        None,
    )
    .unwrap();
    assert_eq!(
        reopened
            .snapshot_receive_status(&first.command_id)
            .unwrap()
            .unwrap(),
        retired
    );
}

/// Expiry and permission loss never renew the original issue, even on byte-identical replay or reopen.
#[test]
fn expired_snapshot_receive_replay_is_readonly_and_header_changes_refuse() {
    let (directory, root, _source, header) = fixture();
    let first = root
        .prepare_snapshot_receive(
            header.clone(),
            ContentBudget::issue(100).unwrap(),
            Arc::new(|| Ok(())),
        )
        .unwrap();
    std::thread::sleep(Duration::from_millis(120));
    let repeated = root
        .prepare_snapshot_receive(
            header.clone(),
            ContentBudget::issue(30000).unwrap(),
            Arc::new(|| Err("revoked".into())),
        )
        .unwrap();
    assert_eq!(first, repeated);
    assert!(
        root.complete_snapshot_receive(&first.command_id, Arc::new(|| Ok(())))
            .is_err()
    );
    let mut changed = header.clone();
    changed.target.epoch.term += 1;
    assert!(
        root.prepare_snapshot_receive(
            changed,
            ContentBudget::issue(30000).unwrap(),
            Arc::new(|| Ok(()))
        )
        .is_err()
    );
    let identity = root.identity().clone();
    drop(root);
    let reopened = ReplicaContentStore::open_with_snapshot_receives(
        &directory.path().join("receiver"),
        &identity.profile_identity,
        identity.member,
        None,
    )
    .unwrap();
    assert_eq!(
        reopened
            .snapshot_receive_status(&first.command_id)
            .unwrap()
            .unwrap(),
        first
    );
    assert!(
        reopened
            .snapshot_receive_scope(&first.command_id, &header)
            .is_err()
    );
}

/// A drained upgrade retains every original row/inode and refuses old writers or missing mandatory journals.
#[test]
fn drained_snapshot_upgrade_preserves_identity_and_missing_head_refuses() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("original");
    let profile = "a".repeat(64);
    let root = ReplicaContentStore::create_with_streams(&path, &profile, 17).unwrap();
    let identity = root.identity().clone();
    assert!(ReplicaContentStore::upgrade_snapshot_receives(&path, &profile, 17, None).is_err());
    drop(root);
    let upgraded =
        ReplicaContentStore::upgrade_snapshot_receives(&path, &profile, 17, None).unwrap();
    assert_eq!(*upgraded.identity(), identity);
    drop(upgraded);
    assert!(ReplicaContentStore::open_with_streams(&path, &profile, 17).is_err());
    let sql = Connection::open(path.join("content.sqlite3")).unwrap();
    sql.execute("DELETE FROM metadata WHERE key='snapshot_receive_ids'", [])
        .unwrap();
    drop(sql);
    assert!(ReplicaContentStore::open_with_snapshot_receives(&path, &profile, 17, None).is_err());
}
