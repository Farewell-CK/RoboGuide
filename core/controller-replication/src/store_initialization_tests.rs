//! Actual original parent/Root SQL generations; no initialization, quorum, Node or device is invented.

use super::*;
use std::os::unix::fs::MetadataExt;

/// An explicit drained upgrade preserves original inodes and fences old constructors at zero commands.
#[test]
fn parent9_drained_upgrade_fences_parent8_without_inventing_application_state() {
    let directory = tempfile::tempdir().unwrap();
    let profile = "a".repeat(64);
    let schema = state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA;
    let root =
        ReplicaContentStore::create_with_staging_gc(&directory.path().join("root"), &profile, 1)
            .unwrap();
    let path = directory.path().join("parent.sqlite3");
    let old = SqliteRaftStore::open_with_controller_content(&path, &profile, schema, root.clone())
        .unwrap();
    let inode = path.metadata().unwrap().ino();
    let root_identity = root.identity().clone();
    assert!(
        SqliteRaftStore::upgrade_content_initialization(
            &path,
            &profile,
            schema,
            root.clone(),
            None
        )
        .is_err()
    );
    drop(old);
    assert!(
        SqliteRaftStore::open_with_content_initialization(
            &path,
            &profile,
            schema,
            root.clone(),
            None
        )
        .is_err()
    );
    let current = SqliteRaftStore::upgrade_content_initialization(
        &path,
        &profile,
        schema,
        root.clone(),
        None,
    )
    .unwrap();
    assert_eq!(path.metadata().unwrap().ino(), inode);
    assert_eq!(root.identity(), &root_identity);
    assert!(current.supports_content_initialization());
    assert!(current.content_initialization_policy().is_none());
    assert!(current.accepted_view().unwrap().is_empty_application());
    drop(current);
    assert!(
        SqliteRaftStore::open_with_controller_content(&path, &profile, schema, root.clone())
            .is_err()
    );
    assert!(
        SqliteRaftStore::open_with_network_content(&path, &profile, schema, root.clone()).is_err()
    );
    SqliteRaftStore::open_with_content_initialization(&path, &profile, schema, root, None).unwrap();
}

/// A declared source is immutable; loss of the mandatory policy is never accepted as unconfigured.
#[test]
fn parent9_policy_is_original_and_missing_mandatory_row_refuses_without_repair() {
    let directory = tempfile::tempdir().unwrap();
    let profile = "a".repeat(64);
    let schema = state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA;
    let root =
        ReplicaContentStore::create_with_staging_gc(&directory.path().join("root"), &profile, 1)
            .unwrap();
    let path = directory.path().join("parent.sqlite3");
    // This is a declaration-only contract fixture, never source existence or content authority.
    let policy = ContentInitializationPolicy {
        schema: "roboguide.content-initialization-policy/v0.1".into(),
        source_state_digest: format!("sha256:{}", "b".repeat(64)),
        source_owner: format!("artifact-store:{}", "c".repeat(64)),
        source_member: 1,
        command_id: "original-declared-initialization".into(),
        budget_ms: 30_000,
    };
    let current = SqliteRaftStore::open_with_content_initialization(
        &path,
        &profile,
        schema,
        root.clone(),
        Some(policy.clone()),
    )
    .unwrap();
    assert_eq!(current.content_initialization_policy(), Some(&policy));
    current
        .initialize_certificate_trust(
            &(1..=3)
                .map(|member| (member, format!("sha256:{member:064x}")))
                .collect(),
        )
        .unwrap();
    assert!(current.accepted_view().unwrap().is_empty_application());
    drop(current);
    assert!(
        SqliteRaftStore::open_with_content_initialization(
            &path,
            &profile,
            schema,
            root.clone(),
            None
        )
        .is_err()
    );
    let mut changed = policy.clone();
    changed.budget_ms += 1;
    assert!(
        SqliteRaftStore::open_with_content_initialization(
            &path,
            &profile,
            schema,
            root.clone(),
            Some(changed)
        )
        .is_err()
    );
    let current = SqliteRaftStore::open_with_content_initialization(
        &path,
        &profile,
        schema,
        root.clone(),
        Some(policy.clone()),
    )
    .unwrap();
    drop(current);
    let sql = Connection::open(&path).unwrap();
    sql.execute(
        "DELETE FROM replica_v1_meta WHERE key=?1",
        [INITIALIZATION_POLICY_KEY],
    )
    .unwrap();
    drop(sql);
    assert!(
        SqliteRaftStore::open_with_content_initialization(
            &path,
            &profile,
            schema,
            root,
            Some(policy)
        )
        .is_err()
    );
    let sql = Connection::open(&path).unwrap();
    assert_eq!(
        sql.query_row(
            "SELECT COUNT(*) FROM replica_v1_meta WHERE key=?1",
            [INITIALIZATION_POLICY_KEY],
            |row| row.get::<_, u64>(0)
        )
        .unwrap(),
        0
    );
}
