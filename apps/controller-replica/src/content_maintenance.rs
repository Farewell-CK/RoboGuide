//! Explicit local OS-admin content maintenance; no listener, consensus or Controller child starts.

use controller_replication::{
    ArtifactContentKey, ContentInitializationPolicy, ReplicaContentStore, ReplicaStagingTarget,
    SqliteRaftStore,
};
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
    /// Explicit Root8 interpretation; absent declarations retain their original Root7 meaning.
    #[serde(default)]
    content_layout: Option<String>,
}

impl Member {
    /// Opens only the explicitly declared GC generation, never detecting or upgrading from files.
    fn open_gc(&self, binding: Option<&str>) -> Result<ReplicaContentStore, String> {
        match self.content_layout.as_deref() {
            None => ReplicaContentStore::open_with_reclamation(
                &self.content_root,
                &self.profile_identity,
                self.member,
                binding,
            ),
            Some(controller_replication::REPLICA_CONTENT_STAGING_GC_LAYOUT_SCHEMA) => {
                ReplicaContentStore::open_with_staging_gc(
                    &self.content_root,
                    &self.profile_identity,
                    self.member,
                    binding,
                )
            }
            Some(_) => Err("unsupported explicit content maintenance layout".into()),
        }
    }

    /// Earlier actions cannot silently accept a declaration that changes their interpretation.
    fn legacy(&self) -> Result<(), String> {
        if self.content_layout.is_some() {
            return Err("original maintenance action does not accept a new layout selector".into());
        }
        Ok(())
    }

    /// Requires the caller's explicit Root8 selection before new staging authority is admitted.
    fn staging(&self) -> Result<(), String> {
        if self.content_layout.as_deref()
            != Some(controller_replication::REPLICA_CONTENT_STAGING_GC_LAYOUT_SCHEMA)
        {
            return Err("staging maintenance requires explicit Root8".into());
        }
        Ok(())
    }
}

/// Exact existing original parent declaration; absent files never bootstrap replacement authority.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayParent {
    /// Explicit parent9 interpretation, independent of domain/Node generations.
    schema: String,
    /// Original existing absolute Raft database.
    path: PathBuf,
    /// Exact original common initialization declaration, including genuine null.
    initialization: Option<ContentInitializationPolicy>,
}

impl ReplayParent {
    /// Acquires the actual existing pair under its complete original policy and physical binding.
    fn open(&self, member: &Member, binding: Option<&str>) -> Result<SqliteRaftStore, String> {
        member.staging()?;
        if self.schema != "roboguide.replica-storage/v0.9"
            || !self.path.is_absolute()
            || self.path.as_os_str().len() > 4096
        {
            return Err("replay inspection requires explicit existing parent9".into());
        }
        SqliteRaftStore::open_existing_with_content_initialization(
            &self.path,
            &member.profile_identity,
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            member.open_gc(binding)?,
            self.initialization.clone(),
        )
    }
}

/// Closed local actions; same-ID adoption retries are observations of the original record.
#[derive(Deserialize)]
#[serde(tag = "operation", rename_all = "kebab-case", deny_unknown_fields)]
enum Request {
    /// Inspects exact committed replay prerequisites without starting Raft, Controller or a Node.
    InspectReplayContent {
        /// Exact existing Root8 receiver.
        member: Member,
        /// Explicit current physical restoration binding.
        restore_binding_digest: Option<String>,
        /// Exact original parent9 declaration.
        parent: ReplayParent,
    },
    /// Copies only an exact original committed input through its stable independent repair operation.
    RepairReplayContent {
        /// Original existing Root8 receiver.
        member: Member,
        /// Exact original restored backing.
        restore_binding_digest: Option<String>,
        /// Complete existing parent9 declaration.
        parent: ReplayParent,
        /// Original actual retained committed Log index.
        log_index: u64,
        /// Complete original retained Log row digest.
        entry_digest: String,
        /// One exact original qualified declared requirement.
        content: ArtifactContentKey,
        /// Actual existing managed Artifact source; no owner is relabelled.
        source_cas: PathBuf,
        /// First immutable copy duration, never renewed by duplicate calls.
        budget_ms: u64,
    },
    /// Releases only a completed original copy's own pin, retaining all independent consumers.
    RetireCompletedContentCopy {
        /// Complete original existing Root7/8 receiver.
        member: Member,
        /// Exact current physical backing if restored.
        restore_binding_digest: Option<String>,
        /// Original retained copy operation ID.
        command_id: String,
    },
    /// Releases only an original reader whose actual recorded process incarnation has ended.
    RetireEndedContentReader {
        /// Complete original accepted Root7/8 declaration.
        member: Member,
        /// Exact original restored backing when selected.
        restore_binding_digest: Option<String>,
        /// Original reader identity, never a caller-supplied process or stop assertion.
        reader_id: String,
    },
    /// Releases an Unknown copy only through independently observed original process-end evidence.
    RetireEndedContentCopy {
        /// Existing original explicitly declared GC root.
        member: Member,
        /// Exact original restored backing when selected.
        restore_binding_digest: Option<String>,
        /// Original copy command, never an asserted caller completion or stop flag.
        command_id: String,
    },
    /// Explicitly drains Root7 and installs mandatory Root8 inventories before any cleanup.
    UpgradeStagingRoot {
        /// Existing original member and explicit desired Root8 interpretation.
        member: Member,
        /// Exact original restored backing, absent for original physical roots.
        restore_binding_digest: Option<String>,
    },
    /// Reclaims only private names derived from exact original retired source operations.
    CollectStaging {
        /// Existing original explicit Root8 declaration.
        member: Member,
        /// Original restored backing if selected.
        restore_binding_digest: Option<String>,
        /// Immutable original cleanup identity; same-ID requests only observe.
        command_id: String,
        /// Bounded typed original selectors, never caller paths or close flags.
        targets: Vec<ReplicaStagingTarget>,
        /// One original queue/scan/effect/SQL duration.
        budget_ms: u64,
    },
    /// Observes exact original intent/items without another filesystem effect or budget.
    ObserveStagingGc {
        /// Existing original explicit Root8 declaration.
        member: Member,
        /// Exact original restored backing if selected.
        restore_binding_digest: Option<String>,
        /// Original retained cleanup command.
        command_id: String,
    },
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
        Request::InspectReplayContent {
            member,
            restore_binding_digest,
            parent,
        } => {
            let store = parent.open(&member, restore_binding_digest.as_deref())?;
            Ok(
                serde_json::json!({"schema":"roboguide.replay-content-inspection/v0.1",
                "unavailable":store.unavailable_replay_inputs()?,"starts_services":false}),
            )
        }
        Request::RepairReplayContent {
            member,
            restore_binding_digest,
            parent,
            log_index,
            entry_digest,
            content,
            source_cas,
            budget_ms,
        } => {
            let store = parent.open(&member, restore_binding_digest.as_deref())?;
            serde_json::to_value(store.repair_committed_input_from_artifact(
                log_index,
                &entry_digest,
                &content,
                &source_cas,
                budget_ms,
            )?)
            .map_err(|error| error.to_string())
        }
        Request::RetireCompletedContentCopy {
            member,
            restore_binding_digest,
            command_id,
        } => {
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.retire_completed_copy(&command_id)?)
                .map_err(|error| error.to_string())
        }
        Request::RetireEndedContentReader {
            member,
            restore_binding_digest,
            reader_id,
        } => {
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.retire_ended_reader(&reader_id)?)
                .map_err(|error| error.to_string())
        }
        Request::RetireEndedContentCopy {
            member,
            restore_binding_digest,
            command_id,
        } => {
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.retire_ended_copy(&command_id)?)
                .map_err(|error| error.to_string())
        }
        Request::UpgradeStagingRoot {
            member,
            restore_binding_digest,
        } => {
            member.staging()?;
            let root = ReplicaContentStore::upgrade_staging_gc(
                &member.content_root,
                &member.profile_identity,
                member.member,
                restore_binding_digest.as_deref(),
            )?;
            Ok(serde_json::json!({"root_identity":root.identity(),
                "staging_layout":controller_replication::REPLICA_CONTENT_STAGING_GC_LAYOUT_SCHEMA,
                "historical_operations":"preserved; no clocks or permissions renewed"}))
        }
        Request::CollectStaging {
            member,
            restore_binding_digest,
            command_id,
            targets,
            budget_ms,
        } => {
            member.staging()?;
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.collect_retired_staging(&command_id, targets, budget_ms)?)
                .map_err(|error| error.to_string())
        }
        Request::ObserveStagingGc {
            member,
            restore_binding_digest,
            command_id,
        } => {
            member.staging()?;
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.staging_gc_status(&command_id)?)
                .map_err(|error| error.to_string())
        }
        Request::RetireContentStream {
            member,
            restore_binding_digest,
            command_id,
            budget_ms,
        } => {
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            root.retire_stream(&command_id, budget_ms)
        }
        Request::UpgradeReclamationRoot {
            member,
            restore_binding_digest,
        } => {
            member.legacy()?;
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
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.collect_unreferenced_content(&command_id, budget_ms)?)
                .map_err(|error| error.to_string())
        }
        Request::ObserveContentGc {
            member,
            restore_binding_digest,
            command_id,
        } => {
            let root = member.open_gc(restore_binding_digest.as_deref())?;
            serde_json::to_value(root.gc_status(&command_id)?).map_err(|error| error.to_string())
        }
        Request::UpgradeSnapshotRoot {
            member,
            restore_binding_digest,
        } => {
            member.legacy()?;
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
            member.legacy()?;
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
            member.legacy()?;
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
