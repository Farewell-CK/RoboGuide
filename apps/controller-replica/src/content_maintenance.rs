//! Explicit local OS-admin content maintenance; no listener, consensus or Controller child starts.

use controller_replication::{ReplicaContentStore, SqliteRaftStore};
use serde::Deserialize;
use std::io::Read;
use std::path::{Path, PathBuf};

/// Closed original storage declaration; arbitrary explicit members are independent of embodied Nodes.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Member {
    /// Original existing absolute content root; no root is created by maintenance.
    content_root: PathBuf,
    /// Exact original infrastructure cluster/profile identity.
    profile_identity: String,
    /// Exact original infrastructure member, never a capability or device inventory.
    member: u64,
}

/// Closed local actions; same-ID adoption retries are observations of the original record.
#[derive(Deserialize)]
#[serde(tag = "operation", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Retires only the exact actually closed stream, preserving its original receipt or Unknown.
    RetireContentStream {
        /// Complete original accepted Root7 declaration.
        member: Member,
        /// Exact restored backing when selected.
        restore_binding_digest: Option<String>,
        /// Original stream command, never a replacement effect identity.
        command_id: String,
        /// Original bounded observation/SQL duration, separate from the original stream budget.
        budget_ms: u64,
    },
    /// Drains original Root6 writers and explicitly enables permanent Root7 GC fences.
    UpgradeReclamationRoot {
        /// Complete existing original member/root declaration.
        member: Member,
        /// Exact accepted original restored backing, absent for the original physical root.
        restore_binding_digest: Option<String>,
    },
    /// Locally admits original inactive selections and permanently fences their qualified keys.
    CollectContent {
        /// Original complete accepted Root7 declaration.
        member: Member,
        /// Exact declared restored backing; no automatic discovery changes interpretation.
        restore_binding_digest: Option<String>,
        /// Immutable command identity; duplicate calls only observe original outcomes.
        command_id: String,
        /// Original duration including scan, SQL, hashing, unlink and durable outcome publication.
        budget_ms: u64,
    },
    /// Observes only the original retained GC command without new effects or budgets.
    ObserveContentGc {
        /// Complete original accepted member/root declaration.
        member: Member,
        /// Explicit restored backing if selected.
        restore_binding_digest: Option<String>,
        /// Original immutable GC command, including retained Unknown.
        command_id: String,
    },
    /// Explicitly drain and upgrade Root4/Root5 to durable Snapshot receive authority, preserving original history.
    UpgradeSnapshotRoot {
        /// Complete existing original member/root declaration.
        member: Member,
        /// Exact original restored backing digest; absent for original Root4.
        restore_binding_digest: Option<String>,
    },
    /// Explicitly upgrade the original drained reader root; parent authority is not upgraded here.
    UpgradeRoot {
        /// Original existing member-local storage declaration.
        member: Member,
    },
    /// Adopt only current original v4 Log/View/Snapshot retention under both actual writers.
    AdoptParent {
        /// Exact already-upgraded original member-local storage.
        member: Member,
        /// Original existing absolute Raft SQL file; another copy cannot lend its original proof.
        parent_sql: PathBuf,
        /// Explicit immutable new management operation, independent of historical producer IDs.
        operation_id: String,
        /// Original nonrenewing effect duration including queueing, SQL and byte verification.
        budget_ms: u64,
    },
}

/// Runs one explicit closed bounded local command, retaining every original writer inside its scope.
pub(crate) fn run(path: &Path) -> Result<serde_json::Value, String> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|error| error.to_string())?
        .take(16 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > 16 * 1024 {
        return Err("content maintenance request exceeds its fixed bound".into());
    }
    let request: Request = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    match request {
        Request::RetireContentStream {
            member,
            restore_binding_digest,
            command_id,
            budget_ms,
        } => {
            let root = ReplicaContentStore::open_with_reclamation(
                &member.content_root,
                &member.profile_identity,
                member.member,
                restore_binding_digest.as_deref(),
            )?;
            root.retire_stream(&command_id, budget_ms)
        }
        Request::UpgradeReclamationRoot {
            member,
            restore_binding_digest,
        } => {
            let root = ReplicaContentStore::upgrade_reclamation(
                &member.content_root,
                &member.profile_identity,
                member.member,
                restore_binding_digest.as_deref(),
            )?;
            Ok(serde_json::json!({"root_identity":root.identity(),
                "reclamation_layout":controller_replication::REPLICA_CONTENT_GC_LAYOUT_SCHEMA,
                "historical_operations":"preserved; no clocks or permissions renewed"}))
        }
        Request::CollectContent {
            member,
            restore_binding_digest,
            command_id,
            budget_ms,
        } => {
            let root = ReplicaContentStore::open_with_reclamation(
                &member.content_root,
                &member.profile_identity,
                member.member,
                restore_binding_digest.as_deref(),
            )?;
            serde_json::to_value(root.collect_unreferenced_content(&command_id, budget_ms)?)
                .map_err(|error| error.to_string())
        }
        Request::ObserveContentGc {
            member,
            restore_binding_digest,
            command_id,
        } => {
            let root = ReplicaContentStore::open_with_reclamation(
                &member.content_root,
                &member.profile_identity,
                member.member,
                restore_binding_digest.as_deref(),
            )?;
            serde_json::to_value(root.gc_status(&command_id)?).map_err(|error| error.to_string())
        }
        Request::UpgradeSnapshotRoot {
            member,
            restore_binding_digest,
        } => {
            let root = ReplicaContentStore::upgrade_snapshot_receives(
                &member.content_root,
                &member.profile_identity,
                member.member,
                restore_binding_digest.as_deref(),
            )?;
            Ok(
                serde_json::json!({"root_identity":root.identity(),"snapshot_receive_layout":controller_replication::REPLICA_CONTENT_SNAPSHOT_LAYOUT_SCHEMA,
                "historical_operations":"preserved; no clocks or permissions renewed"}),
            )
        }
        Request::UpgradeRoot { member } => {
            let root = ReplicaContentStore::upgrade_producer_journal(
                &member.content_root,
                &member.profile_identity,
                member.member,
            )?;
            Ok(
                serde_json::json!({"root_identity":root.identity(), "root_upgraded":true, "parent_adopted":false, "historical_budget":"unavailable; original rows retained"}),
            )
        }
        Request::AdoptParent {
            member,
            parent_sql,
            operation_id,
            budget_ms,
        } => {
            if !parent_sql.is_absolute() {
                return Err(
                    "content parent maintenance requires an explicit absolute SQL file".into(),
                );
            }
            let result = SqliteRaftStore::adopt_original_content_parent_from_root(
                &parent_sql,
                &member.profile_identity,
                state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
                &member.content_root,
                member.member,
                &operation_id,
                budget_ms,
            )?;
            serde_json::to_value(result).map_err(|error| error.to_string())
        }
    }
}
