//! Explicit once-only original archived-source admission through owned real SQL/CAS/quorum work.

use crate::content_store::{ContentBudget, StreamPermission};
use crate::source_initialization::InitializationAdmission;
use crate::*;
use openraft::StoredMembership;
use std::fs::{File, OpenOptions};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

/// Original source descriptors remain leased through the actual owned operation, including cancellation.
struct OriginalSource {
    /// Exact original Controller sibling writer lease.
    _writer: File,
    /// Exact original database inode lease.
    _database: File,
    /// Existing managed CAS owner/lease; opening never creates or upgrades files.
    artifacts: artifact_store::FileSystemArtifactStore,
}

/// Opens only an existing regular source file, refusing symlink replacement before its actual lease.
fn lease_file(path: &Path) -> Result<File, String> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| error.to_string())?;
    file.try_lock()
        .map_err(|error| format!("original source writer is active: {error}"))?;
    let held = file.metadata().map_err(|error| error.to_string())?;
    let actual = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !actual.is_file()
        || !held.is_file()
        || (held.dev(), held.ino()) != (actual.dev(), actual.ino())
    {
        return Err("original source path/inode changed before capture".into());
    }
    Ok(file)
}

impl OriginalSource {
    /// Requires both original Controller leases and the complete existing CAS before source reads.
    fn open(input: &ContentInitializationInput) -> Result<Self, String> {
        input.validate()?;
        let mut sibling = input.source_sql.as_os_str().to_os_string();
        sibling.push(".writer.lock");
        let writer = lease_file(Path::new(&sibling))?;
        let database = lease_file(&input.source_sql)?;
        let artifacts =
            artifact_store::FileSystemArtifactStore::open_existing_managed(&input.source_cas)
                .map_err(|error| error.to_string())?;
        Ok(Self {
            _writer: writer,
            _database: database,
            artifacts,
        })
    }

    /// Rechecks the held original SQL identity before/after its complete actual readonly transaction.
    fn snapshot(
        &self,
        input: &ContentInitializationInput,
    ) -> Result<state::CompleteReplicaState, String> {
        let before = self
            ._database
            .metadata()
            .map_err(|error| error.to_string())?;
        let check = || {
            let current =
                std::fs::symlink_metadata(&input.source_sql).map_err(|error| error.to_string())?;
            if !current.is_file() || (current.dev(), current.ino()) != (before.dev(), before.ino())
            {
                return Err("original source SQL inode changed during capture".to_string());
            }
            Ok(())
        };
        check()?;
        let original = state::SqliteEventLog::read_complete_replica_state(&input.source_sql)
            .map_err(|error| error.to_string())?;
        check()?;
        Ok(original)
    }
}

/// Inspects only existing quiescent source metadata; it grants no initialization or payload receipt.
pub fn inspect_archived_source(
    input: &ContentInitializationInput,
) -> Result<ContentInitializationSourceObservation, String> {
    let source = OriginalSource::open(input)?;
    let original = source.snapshot(input)?;
    let result = ContentInitializationSourceObservation {
        schema: "roboguide.content-initialization-source-observation/v0.1".into(),
        source_state_digest: original.identity().map_err(|error| error.to_string())?,
        source_owner: source
            .artifacts
            .owner_id()
            .ok_or("actual original source owner is missing")?
            .into(),
        history_floor: original
            .recovery
            .history_floor()
            .map_err(|error| error.to_string())?,
        event_sequence: original.checkpoint.event_sequence,
        authority_revision: original
            .commands
            .last()
            .ok_or("original source command evidence is missing")?
            .authority_revision,
    };
    if result.history_floor == 0 {
        return Err(
            "explicit archived-source initialization requires an original archived prefix".into(),
        );
    }
    Ok(result)
}

impl ReplicaNode {
    /// Observes original first operation evidence on an owned worker, without source I/O or budget renewal.
    pub async fn content_initialization_status(
        &self,
    ) -> Result<Option<ContentInitializationStatus>, String> {
        let store = self.store.clone();
        tokio::task::spawn_blocking(move || store.content_initialization_status())
            .await
            .map_err(|error| error.to_string())?
    }
    /// Rechecks actual original leader/committed membership without inventing a source or byte fact.
    fn initialization_authority(
        &self,
        epoch: ControllerEpoch,
        membership: &StoredMembership<u64, ReplicaMember>,
    ) -> Result<(), String> {
        let (_, actual) = self.store.try_network_authority()?;
        let metrics = self.raft.metrics().borrow().clone();
        if actual != *membership
            || metrics.membership_config.as_ref() != membership
            || metrics.current_term != epoch.term
            || metrics.current_leader != Some(epoch.replica_id)
        {
            return Err("original source initialization epoch/membership changed".into());
        }
        self.store
            .validate_content_membership(membership.membership())
    }

    /// Requires actual parent9/exact policy on every requested current or proposed voter before effects.
    pub(crate) async fn require_initialization_members(
        &self,
        members: BTreeSet<u64>,
        epoch: ControllerEpoch,
        clock: &ContentBudget,
        permission: StreamPermission,
    ) -> Result<(), String> {
        let Some(policy) = self.store.content_initialization_policy().cloned() else {
            return Ok(());
        };
        let (_, membership) = self.store.try_network_authority()?;
        self.initialization_authority(epoch, &membership)?;
        let mut replies = tokio::task::JoinSet::new();
        for member in members {
            if member == self.config.replica_id {
                if !self.store.supports_content_initialization() {
                    return Err("original local parent9 is unavailable".into());
                }
                continue;
            }
            let node = self.clone();
            let expected = policy.clone();
            let membership = membership.clone();
            let clock = clock.clone();
            let caller = permission.clone();
            replies.spawn(async move {
                clock.check()?;
                caller()?;
                let actual = transport::initialization_contract(
                    &node,
                    member,
                    epoch,
                    membership,
                    clock.remaining_ms()?,
                )
                .await?;
                clock.check()?;
                caller()?;
                if actual.as_ref() != Some(&expected) {
                    return Err(format!(
                        "actual member {member} lacks the exact original parent9 policy"
                    ));
                }
                Ok::<(), String>(())
            });
        }
        while let Some(reply) = replies.join_next().await {
            reply.map_err(|error| error.to_string())??;
        }
        clock.check()?;
        permission()?;
        self.initialization_authority(epoch, &membership)
    }

    /// Admits one durable source operation; retries observe original results and never re-read/re-submit.
    pub async fn initialize_archived_source(
        &self,
        input: ContentInitializationInput,
    ) -> Result<ContentInitializationStatus, String> {
        input.validate()?;
        let policy = self
            .store
            .content_initialization_policy()
            .cloned()
            .ok_or("explicit original source initialization policy is missing")?;
        if self.config.replica_id != policy.source_member {
            return Err("only the original configured source member can initialize".into());
        }
        let original_input = input.clone();
        let store = self.store.clone();
        if let Some(status) = tokio::task::spawn_blocking(move || {
            store.existing_content_initialization(&original_input)
        })
        .await
        .map_err(|error| error.to_string())??
        {
            return Ok(status);
        }
        let clock = ContentBudget::issue(policy.budget_ms)?;
        let original_clock = clock.clone();
        let node = self.clone();
        self.run_content_transition(clock, async move {
            node.initialize_source_owned(input, original_clock).await
        })
        .await
    }

    /// Retains the original transition, source descriptors and first clock until all actual work ends.
    async fn initialize_source_owned(
        &self,
        input: ContentInitializationInput,
        clock: ContentBudget,
    ) -> Result<ContentInitializationStatus, String> {
        let epoch = self
            .current_epoch()
            .ok_or("original source member is not the actual current leader")?;
        self.check_epoch(epoch).await?;
        let (_, membership) = self.store.try_network_authority()?;
        let held = self.clone();
        let expected = membership.clone();
        let first_clock = clock.clone();
        let permission: StreamPermission = Arc::new(move || {
            first_clock.check()?;
            held.initialization_authority(epoch, &expected)
        });
        self.require_initialization_members(
            membership.voter_ids().collect(),
            epoch,
            &clock,
            permission.clone(),
        )
        .await?;
        let store = self.store.clone();
        let first_clock = clock.clone();
        let caller = permission.clone();
        let admission = tokio::task::spawn_blocking(move || {
            store.admit_content_initialization(input, epoch, first_clock, caller)
        })
        .await
        .map_err(|error| error.to_string())??;
        let record = match admission {
            InitializationAdmission::Existing(status) => return Ok(*status),
            InitializationAdmission::New(record) => *record,
        };
        let node = self.clone();
        let caller = permission.clone();
        let (record, source) = tokio::task::spawn_blocking(move || {
            caller()?;
            let source = OriginalSource::open(&record.intent.input)?;
            let original = source.snapshot(&record.intent.input)?;
            caller()?;
            let candidate = ContentApplicationBatch::from_archived_source(
                &node.profile_identity(),
                record.intent.epoch,
                &source.artifacts,
                &ReplicaView::default(),
                original,
            )?;
            let captured = node
                .store
                .capture_content_initialization(record, candidate, caller)?;
            Ok::<_, String>((captured, source))
        })
        .await
        .map_err(|error| error.to_string())??;
        let candidate = record
            .captured
            .as_ref()
            .ok_or("original captured source disappeared")?
            .candidate
            .as_ref()
            .clone();
        self.stage_content_from_source_checked(
            &candidate,
            &source.artifacts,
            &record.intent.policy.command_id,
            clock.clone(),
            permission.clone(),
        )
        .await?;
        let (receipt, members) = self
            .commit_content_serialized(candidate, clock, permission.clone())
            .await?;
        let store = self.store.clone();
        tokio::task::spawn_blocking(move || {
            let _source = source;
            store.complete_content_initialization(record, receipt, members, permission)
        })
        .await
        .map_err(|error| error.to_string())?
    }
}
