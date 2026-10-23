//! Exact consensus member data; a declared software content owner is never a byte or device proof.

use serde::{Deserialize, Serialize};

/// Infrastructure member metadata retains the original address-only encoding when binding is absent.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaMember {
    /// Original declared infrastructure endpoint, never an embodied Node selector.
    pub addr: String,
    /// Exact member-local software store binding; absent legacy records confer no content authority.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content_store_owner: Option<String>,
}

impl ReplicaMember {
    /// Preserves the original BasicNode address-only data without creating a content binding.
    pub fn new(addr: impl ToString) -> Self {
        Self {
            addr: addr.to_string(),
            content_store_owner: None,
        }
    }

    /// Constructs only declared complete member data; real adapters must independently prove its root.
    pub fn with_content_store(addr: impl ToString, store_owner: String) -> Result<Self, String> {
        let member = Self {
            addr: addr.to_string(),
            content_store_owner: Some(store_owner),
        };
        member.validate_content_binding()?;
        Ok(member)
    }

    /// Requires a bounded actual endpoint and full qualified software store identity, never an `ok` flag.
    pub fn validate_content_binding(&self) -> Result<(), String> {
        if self.addr.len() > 128
            || self
                .addr
                .parse::<std::net::SocketAddr>()
                .map_or(true, |address| address.port() == 0)
            || self
                .content_store_owner
                .as_deref()
                .and_then(|value| value.strip_prefix("replica-content:"))
                .is_none_or(|value| !crate::content::hex_digest(value))
        {
            return Err("consensus content member lacks its exact endpoint/store binding".into());
        }
        Ok(())
    }
}

/// Current legacy profiles never acquire a new content binding through serde compatibility.
pub(crate) fn require_legacy_membership(
    membership: &openraft::Membership<u64, ReplicaMember>,
) -> Result<(), String> {
    if membership
        .nodes()
        .any(|(_, member)| member.content_store_owner.is_some())
    {
        return Err(
            "consensus content-owner membership requires its explicit full-content profile".into(),
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use openraft::{
        BasicNode, CommittedLeaderId, LogId, Membership, SnapshotMeta, StoredMembership,
    };
    use std::collections::{BTreeMap, BTreeSet};

    /// Original member and complete membership/Snapshot encodings remain byte-for-byte identical.
    #[test]
    fn legacy_member_membership_and_snapshot_encodings_keep_the_original_bytes() {
        let address = "127.0.0.1:32191";
        let old = BasicNode::new(address);
        let current = ReplicaMember::new(address);
        assert_eq!(
            serde_json::to_vec(&old).unwrap(),
            serde_json::to_vec(&current).unwrap()
        );
        let old = StoredMembership::new(
            Some(LogId::new(CommittedLeaderId::new(2, 7), 4)),
            Membership::new(vec![BTreeSet::from([7])], BTreeMap::from([(7, old)])),
        );
        let current = StoredMembership::new(
            *old.log_id(),
            Membership::new(vec![BTreeSet::from([7])], BTreeMap::from([(7, current)])),
        );
        assert_eq!(
            serde_json::to_vec(&old).unwrap(),
            serde_json::to_vec(&current).unwrap()
        );
        let old = SnapshotMeta {
            last_log_id: *old.log_id(),
            last_membership: old,
            snapshot_id: "original-snapshot".into(),
        };
        let current = SnapshotMeta {
            last_log_id: *current.log_id(),
            last_membership: current,
            snapshot_id: "original-snapshot".into(),
        };
        assert_eq!(
            serde_json::to_vec(&old).unwrap(),
            serde_json::to_vec(&current).unwrap()
        );
        let restored: SnapshotMeta<u64, ReplicaMember> =
            serde_json::from_slice(&serde_json::to_vec(&old).unwrap()).unwrap();
        assert_eq!(restored, current);
        require_legacy_membership(current.last_membership.membership()).unwrap();
    }

    /// Missing/invalid owner bindings never grant content authority and old profiles reject valid new ones.
    #[test]
    fn legacy_absence_and_new_bindings_remain_separate_interpretations() {
        assert!(
            ReplicaMember::new("127.0.0.1:32191")
                .validate_content_binding()
                .is_err()
        );
        for owner in [
            "",
            "replica-content:short",
            &format!("artifact-store:{}", "1".repeat(64)),
        ] {
            assert!(ReplicaMember::with_content_store("127.0.0.1:32191", owner.into()).is_err());
        }
        let member = ReplicaMember::with_content_store(
            "127.0.0.1:32191",
            format!("replica-content:{}", "1".repeat(64)),
        )
        .unwrap();
        let membership = Membership::new(vec![BTreeSet::from([7])], BTreeMap::from([(7, member)]));
        assert!(require_legacy_membership(&membership).is_err());
    }

    /// Actual legacy SQL rejects enriched log/apply/Snapshot messages before creating any stored record.
    #[tokio::test]
    async fn legacy_actual_storage_refuses_hidden_member_bindings_before_log_or_snapshot_effects() {
        use crate::{ControllerRaftConfig, ReplicaSnapshotData, ReplicaView, SqliteRaftStore};
        use openraft::storage::RaftStateMachine;
        use openraft::{Entry, EntryPayload};
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("original-legacy.sqlite3");
        let profile = "a".repeat(64);
        let mut store = SqliteRaftStore::open(&path, &profile, "legacy-contract-fixture").unwrap();
        let member = ReplicaMember::with_content_store(
            "127.0.0.1:32191",
            format!("replica-content:{}", "1".repeat(64)),
        )
        .unwrap();
        let membership = Membership::new(vec![BTreeSet::from([7])], BTreeMap::from([(7, member)]));
        let log_id = LogId::new(CommittedLeaderId::new(2, 7), 4);
        let entry: Entry<ControllerRaftConfig> = Entry {
            log_id,
            payload: EntryPayload::Membership(membership.clone()),
        };
        assert!(
            store
                .persist_log_entries(std::slice::from_ref(&entry))
                .unwrap_err()
                .to_string()
                .contains("explicit full-content profile")
        );
        assert!(
            store
                .apply([entry])
                .await
                .unwrap_err()
                .to_string()
                .contains("explicit full-content profile")
        );
        let stored = StoredMembership::new(Some(log_id), membership);
        let body = serde_json::json!({"profile":profile,"last_applied":log_id,"membership":stored,"view":ReplicaView::default()});
        let meta = SnapshotMeta {
            last_log_id: Some(log_id),
            last_membership: stored,
            snapshot_id: "explicit-rejection-fixture-no-quorum".into(),
        };
        assert!(
            store
                .install_snapshot(
                    &meta,
                    Box::new(ReplicaSnapshotData::new(serde_json::to_vec(&body).unwrap()))
                )
                .await
                .unwrap_err()
                .to_string()
                .contains("explicit full-content profile")
        );
        let sql = store.connection.lock().unwrap();
        assert_eq!(
            sql.query_row("SELECT COUNT(*) FROM replica_v1_log", [], |row| row
                .get::<_, u64>(0))
                .unwrap(),
            0
        );
        assert_eq!(
            sql.query_row("SELECT COUNT(*) FROM replica_v1_snapshot", [], |row| row
                .get::<_, u64>(0))
                .unwrap(),
            0
        );
        assert_eq!(
            sql.query_row(
                "SELECT COUNT(*) FROM replica_v1_meta WHERE key IN ('applied','membership')",
                [],
                |row| row.get::<_, u64>(0)
            )
            .unwrap(),
            0
        );
    }
}
