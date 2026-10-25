//! Explicit original-profile application generation authority; process exit never means device stopping.

// OpenRaft requires its concrete StorageError at the actual apply/SQL boundaries.
#![allow(clippy::result_large_err)]

use crate::content_store::ContentBudget;
use crate::store::{io_error, read_meta, write_meta};
use crate::*;
use openraft::StoredMembership;
use rusqlite::{Connection, TransactionBehavior};
use sha2::{Digest as _, Sha256};
use std::path::{Path, PathBuf};

/// Explicit old-writer fence for member-local upgrade intents and committed generation authority.
pub(crate) const STORAGE_VERSION: i64 = 10;
/// Exact managed parent interpretation; original base parent8/9 identity remains unchanged.
pub(crate) const STORAGE_SCHEMA: &str = "roboguide.replica-storage/v0.10";
/// Original bounded operational history, never replaced by a Snapshot.
const LOCAL_KEY: &str = "application_upgrade_operations_v1";
/// Quorum-ordered active writer history, copied only through validated Raft state.
pub(crate) const AUTHORITY_KEY: &str = "application_upgrade_authority_v1";
/// Original parent interpretation retained when the explicit old-writer fence is first installed.
const BASE_PARENT_KEY: &str = "application_upgrade_base_parent_v1";
/// Exact startup policy already pinned by the original cluster profile; never imported from a Snapshot.
const ORIGINAL_POLICY_KEY: &str = "application_upgrade_original_policy_v1";
/// Complete bounded original upgrade history; IDs are never discarded to make room.
const MAX_OPERATIONS: usize = 32;

/// Finite original-generation reading support is independent of new application write permission.
pub fn supports_application_generation(value: &str) -> bool {
    matches!(
        value,
        "roboguide.controller-checkpoint/v53"
            | "roboguide.controller-checkpoint/v54"
            | "roboguide.controller-checkpoint/v55"
            | "roboguide.controller-checkpoint/v56"
            | "roboguide.controller-checkpoint/v57"
            | "roboguide.controller-checkpoint/v58"
            | "roboguide.controller-checkpoint/v59"
            | "roboguide.controller-checkpoint/v60"
    )
}

/// Permits only a finite original source generation at or before the selected actual reader.
pub(crate) fn can_read_generation(source: &str, reader: &str) -> bool {
    supports_application_generation(source)
        && supports_application_generation(reader)
        && source
            .rsplit_once('v')
            .and_then(|(_, value)| value.parse::<u64>().ok())
            <= reader
                .rsplit_once('v')
                .and_then(|(_, value)| value.parse::<u64>().ok())
}

impl ReplicaView {
    /// Reads only the exact immutable source checkpoint pinned by the independently validated initialization anchor.
    pub(crate) fn permits_original_source_checkpoint(
        &self,
        checkpoint: &state::PersistedCheckpoint,
        reader: &str,
    ) -> bool {
        let active = self
            .application_upgrade
            .as_ref()
            .map_or(reader, ApplicationUpgradeAuthority::active_schema);
        can_read_generation(&checkpoint.schema, active)
            && self
                .content
                .as_ref()
                .and_then(|content| content.source_initialization.as_ref())
                .is_some_and(|anchor| {
                    anchor.checkpoint_digest == state::checkpoint_identity(checkpoint)
                        && anchor.event_sequence == checkpoint.event_sequence
                })
    }
}

/// Actual configured next executable and its complete deployment policy identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpgradeBinary {
    /// Exact supported target application schema, independently read from the actual executable.
    pub application_schema: String,
    /// Actual executable file digest; a caller assertion alone never becomes this receipt.
    pub binary_digest: String,
    /// Complete actual target deployment policy digest, independent of the original profile.
    pub policy_digest: String,
}

/// Member-local configured path; never accepted from a network upgrade command.
#[derive(Clone)]
pub(crate) struct LocalUpgradeBinary {
    /// Explicit existing executable path, retained only in this actual process.
    pub(crate) path: PathBuf,
    /// Independently verified original file/schema/policy evidence.
    pub(crate) binary: UpgradeBinary,
    /// Actual original file identity verified beside the complete startup hash; never serialized as authority.
    pub(crate) witness: BinaryWitness,
}

/// Original immutable executable file observation; replacements or metadata changes require fresh startup verification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BinaryWitness {
    /// Exact device/inode/length/full mtime/full ctime/mode, excluding unrelated access time.
    values: (u64, u64, u64, i64, i64, i64, i64, u32),
}

impl BinaryWitness {
    /// Captures only a bounded actual regular executable leaf without claiming hardware attestation.
    pub(crate) fn capture(path: &Path) -> Result<Self, String> {
        use std::os::unix::fs::MetadataExt as _;
        let metadata = std::fs::metadata(path).map_err(|error| error.to_string())?;
        if !metadata.is_file() || metadata.len() == 0 || metadata.len() > 2 * 1024 * 1024 * 1024 {
            return Err("upgrade executable is not a bounded actual regular file".into());
        }
        Ok(Self {
            values: (
                metadata.dev(),
                metadata.ino(),
                metadata.len(),
                metadata.mtime(),
                metadata.mtime_nsec(),
                metadata.ctime(),
                metadata.ctime_nsec(),
                metadata.mode(),
            ),
        })
    }
    /// Reuses the original complete hash only while this exact actual immutable file observation survives.
    pub(crate) fn require_current(&self, path: &Path) -> Result<(), String> {
        if &Self::capture(path)? != self {
            return Err("original verified upgrade executable was replaced or modified".into());
        }
        Ok(())
    }
}

/// Closed administrative request; no support, process-exit or permission flags are accepted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeRequest {
    /// Explicit new administrative interpretation.
    pub schema: String,
    /// Original immutable operation identity, including every retry or observation.
    pub command_id: String,
    /// Exact original committed upgrade revision.
    pub expected_revision: u64,
    /// Currently admitted application writer, not the current executable's preferred version.
    pub from_schema: String,
    /// Actual locally configured target executable and deployment policy.
    pub target: UpgradeBinary,
    /// First receive-relative effect budget; later observations cannot renew it.
    pub budget_ms: u64,
}

/// Actual authenticated member support bound to one original membership and predecessor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeSupport {
    /// Actual member contacted over its authenticated endpoint.
    pub member: u64,
    /// Actual TLS leaf captured by the observing leader's connection.
    pub leaf: String,
    /// Actual infrastructure executable bytes serving the endpoint.
    pub infrastructure_binary_digest: String,
    /// Actual member-local configured target; missing configuration refuses support.
    pub target: UpgradeBinary,
    /// Original consensus membership, including exact declared Root owners.
    pub membership: StoredMembership<u64, ReplicaMember>,
    /// Exact original complete application predecessor.
    pub predecessor: AcceptedApplicationIdentity,
}

/// Complete first operation input; every member retains these exact bytes before stopping a child.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeIntent {
    /// Original public request.
    pub request: ApplicationUpgradeRequest,
    /// Original current leader and term; a new term cannot borrow this operation's budget.
    pub epoch: ControllerEpoch,
    /// Complete original current membership and Root ownership.
    pub membership: StoredMembership<u64, ReplicaMember>,
    /// Original application revision/sequence/digest, independently checked by every member.
    pub predecessor: AcceptedApplicationIdentity,
    /// Actual all-configured-member authenticated support, obtained before any drain intent.
    pub supports: Vec<ApplicationUpgradeSupport>,
}

/// Independent original member wait result; terminal application flags never substitute for it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeDrain {
    /// Actual original infrastructure member.
    pub member: u64,
    /// Actual peer leaf used for the drain observation.
    pub leaf: String,
    /// Digest of the immutable original complete intent.
    pub intent_digest: String,
    /// Actual original child wait or exact scoped absence under the original process journal.
    pub proof: ControllerDrainProof,
}

/// Quorum-ordered generation switch; application revision and old checkpoint bytes do not change here.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeReceipt {
    /// Complete original operation, including immutable observed support and predecessor.
    pub intent: ApplicationUpgradeIntent,
    /// Actual complete original member drain proofs.
    pub drains: Vec<ApplicationUpgradeDrain>,
    /// Monotonic independent upgrade revision.
    pub revision: u64,
}

/// Snapshot-backed independent writer authority; historical absence retains the exact original schema.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeAuthority {
    /// Explicit interpretation, never inferred from a checkpoint version string.
    pub schema: String,
    /// Immutable original application generation from the actual original Replica profile.
    pub original_schema: String,
    /// Complete accepted original upgrade history, independent of domain event compaction.
    pub receipts: Vec<ApplicationUpgradeReceipt>,
    /// Quorum-ordered abandonment of original uncertain operations, never a downgrade or erased wait.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub aborts: Vec<ApplicationUpgradeAbort>,
}

/// Explicit reduction of original pending upgrade permission under a current independent quorum.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationUpgradeAbort {
    /// Original immutable abandonment operation identity.
    pub command_id: String,
    /// Complete independently observed original pending intent.
    pub intent: ApplicationUpgradeIntent,
    /// Actual current leader proof, independent of the original possibly expired epoch.
    pub epoch: ControllerEpoch,
    /// Exact current original membership under which abandonment is linearized.
    pub membership: StoredMembership<u64, ReplicaMember>,
}

/// Original operational issue survives cancellation, process restart and Snapshot installation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LocalUpgradeOperation {
    /// Immutable first original complete source intent.
    pub(crate) intent: ApplicationUpgradeIntent,
    /// Original local receive clock; first queue and waiting time consume this bound.
    pub(crate) clock: ContentBudget,
    /// Original local child incarnation; absence is frozen under the original process writer.
    pub(crate) process: Option<ControllerProcessIdentity>,
    /// Complete original process-journal prefix at first admit, fixed under the same writer.
    pub(crate) process_history_head: u64,
    /// Actual original admitting supervisor incarnation, never replaced by a retry/restart process.
    pub(crate) supervisor_instance: String,
    /// Original independently observed actual wait, retained even after the effect budget expires.
    pub(crate) drain: Option<ApplicationUpgradeDrain>,
}

/// Hashes exact typed original operation bytes without discarding clocks, members or source identity.
pub(crate) fn intent_digest(intent: &ApplicationUpgradeIntent) -> String {
    format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json::to_vec(&serde_json::to_value(intent).expect("typed upgrade serializes"))
                .expect("canonical original upgrade serializes")
        )
    )
}

impl UpgradeBinary {
    /// Refuses empty/future schema, incomplete hashes and caller-sized policy identities.
    pub(crate) fn validate(&self) -> Result<(), String> {
        if !supports_application_generation(&self.application_schema)
            || !crate::certificates::valid_leaf(&self.binary_digest)
            || self.policy_digest.len() != 64
            || !self
                .policy_digest
                .bytes()
                .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
        {
            return Err("upgrade target schema/binary/policy is unsupported or incomplete".into());
        }
        Ok(())
    }
}

impl ApplicationUpgradeIntent {
    /// Checks complete original immutable linkage; this alone does not establish fresh quorum or TLS.
    pub(crate) fn validate(&self) -> Result<(), String> {
        let request = &self.request;
        request.target.validate()?;
        let generation = |schema: &str| {
            schema
                .rsplit_once('v')
                .and_then(|(_, value)| value.parse::<u64>().ok())
        };
        let members = self
            .membership
            .nodes()
            .map(|(id, _)| *id)
            .collect::<BTreeSet<_>>();
        if request.schema != "roboguide.application-upgrade/v0.1"
            || request.command_id.trim().is_empty()
            || request.command_id.len() > 128
            || request.command_id.chars().any(char::is_control)
            || !(100..=300_000).contains(&request.budget_ms)
            || !supports_application_generation(&request.from_schema)
            || generation(&request.target.application_schema) < generation(&request.from_schema)
            || self.epoch.term == 0
            || !members.contains(&self.epoch.replica_id)
            || !(3..=9).contains(&members.len())
            || (self.predecessor.checkpoint_digest.is_none()
                && (self.predecessor.revision != 0 || self.predecessor.event_sequence != 0))
            || self
                .predecessor
                .checkpoint_digest
                .as_deref()
                .is_some_and(|digest| {
                    !crate::content::hex_digest(digest) || self.predecessor.revision == 0
                })
            || self.supports.len() != members.len()
            || self
                .supports
                .iter()
                .map(|proof| proof.member)
                .collect::<BTreeSet<_>>()
                != members
            || self.supports.iter().any(|proof| {
                proof.target != request.target
                    || proof.membership != self.membership
                    || proof.predecessor != self.predecessor
                    || !crate::certificates::valid_leaf(&proof.leaf)
                    || !crate::certificates::valid_leaf(&proof.infrastructure_binary_digest)
            })
        {
            return Err(
                "original application upgrade support/epoch/membership/predecessor differs".into(),
            );
        }
        for (_, member) in self.membership.nodes() {
            member.validate_content_binding()?;
        }
        Ok(())
    }
}

impl ApplicationUpgradeAuthority {
    /// Returns the exact latest permitted application writer, without granting current application ownership.
    pub fn active_schema(&self) -> &str {
        self.receipts.last().map_or(&self.original_schema, |value| {
            &value.intent.request.target.application_schema
        })
    }
    /// Validates the complete monotonic original upgrade history without probing current processes.
    pub(crate) fn validate(&self, original: &str) -> Result<(), String> {
        if self.schema != "roboguide.application-upgrade-authority/v0.1"
            || self.original_schema != original
            || !supports_application_generation(original)
            || (self.receipts.is_empty() && self.aborts.is_empty())
            || self.receipts.len() + self.aborts.len() > MAX_OPERATIONS
        {
            return Err("original application upgrade authority is incomplete".into());
        }
        let mut previous = original;
        let mut ids = BTreeSet::new();
        let mut binaries = BTreeSet::new();
        for (index, receipt) in self.receipts.iter().enumerate() {
            receipt.intent.validate()?;
            let request = &receipt.intent.request;
            if request.from_schema != previous
                || request.expected_revision != index as u64
                || receipt.revision != index as u64 + 1
                || !ids.insert(&request.command_id)
                || !binaries.insert(&request.target.binary_digest)
                || receipt.drains.len() != receipt.intent.supports.len()
                || receipt
                    .drains
                    .iter()
                    .map(|proof| proof.member)
                    .collect::<BTreeSet<_>>()
                    != receipt
                        .intent
                        .supports
                        .iter()
                        .map(|proof| proof.member)
                        .collect()
            {
                return Err(
                    "application upgrade history is discontinuous or lacks original drains".into(),
                );
            }
            for drain in &receipt.drains {
                let support = receipt
                    .intent
                    .supports
                    .iter()
                    .find(|proof| proof.member == drain.member)
                    .ok_or("upgrade drain member is absent")?;
                drain.proof.validate(receipt.intent.epoch)?;
                if drain.leaf != support.leaf
                    || drain.intent_digest != intent_digest(&receipt.intent)
                {
                    return Err("original application upgrade drain lost its exact source".into());
                }
            }
            previous = &request.target.application_schema;
        }
        let mut aborts = BTreeSet::new();
        for abort in &self.aborts {
            abort.intent.validate()?;
            if abort.command_id.trim().is_empty()
                || abort.command_id.len() > 128
                || abort.epoch.term == 0
                || !abort
                    .membership
                    .nodes()
                    .any(|(id, _)| *id == abort.epoch.replica_id)
                || abort.membership != abort.intent.membership
                || !aborts.insert(&abort.command_id)
                || !ids.insert(&abort.intent.request.command_id)
            {
                return Err("original application upgrade abandonment history is incomplete or overlaps activation".into());
            }
        }
        Ok(())
    }
    /// Allows only the exact frozen old predecessor until normal application migration writes the new schema.
    pub(crate) fn permits_checkpoint(&self, checkpoint: &state::PersistedCheckpoint) -> bool {
        checkpoint.schema == self.active_schema()
            || self.receipts.last().is_some_and(|receipt| {
                can_read_generation(&checkpoint.schema, self.active_schema())
                    && Some(state::checkpoint_identity(checkpoint))
                        == receipt.intent.predecessor.checkpoint_digest
                    && checkpoint.event_sequence == receipt.intent.predecessor.event_sequence
            })
    }
}

/// Independently hashes the actual original regular executable with bounded reads and stable file identity.
pub(crate) fn binary_digest(path: &Path) -> Result<String, String> {
    use std::io::Read as _;
    use std::os::unix::fs::MetadataExt as _;
    let mut file = std::fs::File::open(path).map_err(|error| error.to_string())?;
    let original = file.metadata().map_err(|error| error.to_string())?;
    if !original.is_file() || original.len() == 0 || original.len() > 2 * 1024 * 1024 * 1024 {
        return Err("upgrade executable is not a bounded actual regular file".into());
    }
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    let mut length = 0_u64;
    loop {
        let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        length += count as u64;
        if length > original.len() {
            return Err("upgrade executable changed while reading".into());
        }
        digest.update(&buffer[..count]);
    }
    let current = file.metadata().map_err(|error| error.to_string())?;
    let named = std::fs::metadata(path).map_err(|error| error.to_string())?;
    if length != original.len()
        || (
            current.dev(),
            current.ino(),
            current.len(),
            current.mtime(),
            current.mtime_nsec(),
            current.ctime(),
            current.ctime_nsec(),
        ) != (
            original.dev(),
            original.ino(),
            original.len(),
            original.mtime(),
            original.mtime_nsec(),
            original.ctime(),
            original.ctime_nsec(),
        )
        || (named.dev(), named.ino()) != (original.dev(), original.ino())
    {
        return Err("original upgrade executable identity changed".into());
    }
    Ok(format!("sha256:{:x}", digest.finalize()))
}

/// Installs the new writer fence in the same original SQL transaction as its first new authority.
pub(crate) fn install_format(connection: &Connection) -> Result<(), String> {
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if version == STORAGE_VERSION {
        return Ok(());
    }
    if !matches!(version, 8 | 9) {
        return Err("application upgrade requires original managed parent8/9".into());
    }
    write_meta(connection, BASE_PARENT_KEY, &version).map_err(|error| error.to_string())?;
    write_meta(connection, LOCAL_KEY, &Vec::<LocalUpgradeOperation>::new())
        .map_err(|error| error.to_string())?;
    write_meta(
        connection,
        AUTHORITY_KEY,
        &Option::<ApplicationUpgradeAuthority>::None,
    )
    .map_err(|error| error.to_string())?;
    connection.execute_batch("DROP TABLE replica_storage_format; CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version=10),schema TEXT NOT NULL); INSERT INTO replica_storage_format VALUES (10,'roboguide.replica-storage/v0.10'); PRAGMA user_version=10;").map_err(|error| error.to_string())
}

/// Validates mandatory original operational authority without creating an empty replacement history.
pub(crate) fn validate_storage(
    connection: &Connection,
    base_parent: i64,
    original: &str,
) -> Result<(), String> {
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if version != STORAGE_VERSION {
        for key in [LOCAL_KEY, AUTHORITY_KEY, BASE_PARENT_KEY] {
            if read_meta::<serde_json::Value>(connection, key)
                .map_err(|error| error.to_string())?
                .is_some()
            {
                return Err(
                    "old Replica parent contains hidden application upgrade authority".into(),
                );
            }
        }
        return Ok(());
    }
    if read_meta::<i64>(connection, BASE_PARENT_KEY).map_err(|error| error.to_string())?
        != Some(base_parent)
    {
        return Err("application upgrade changed original parent interpretation".into());
    }
    let operations: Vec<LocalUpgradeOperation> = read_meta(connection, LOCAL_KEY)
        .map_err(|error| error.to_string())?
        .ok_or("original application upgrade operational history is absent")?;
    if operations.len() > MAX_OPERATIONS {
        return Err("original application upgrade history exceeds its bound".into());
    }
    let mut ids = BTreeSet::new();
    for operation in &operations {
        operation.intent.validate()?;
        operation.clock.validate()?;
        if !crate::supervision::valid_instance(&operation.supervisor_instance)
            || operation.clock.duration_ms > operation.intent.request.budget_ms
        {
            return Err("original upgrade clock/instance exceeds its original request".into());
        }
        crate::supervision::validate_upgrade_process(
            connection,
            operation.intent.epoch,
            operation.process.as_ref(),
            operation.process_history_head,
            operation.drain.as_ref().map(|drain| &drain.proof),
        )?;
        if let Some(drain) = &operation.drain
            && (drain.intent_digest != intent_digest(&operation.intent)
                || !operation
                    .intent
                    .supports
                    .iter()
                    .any(|support| support.member == drain.member && support.leaf == drain.leaf)
                || matches!(&drain.proof, ControllerDrainProof::NoApplicationProcess { supervisor_instance, .. } if supervisor_instance != &operation.supervisor_instance))
        {
            return Err("original local upgrade wait/leaf/instance linkage differs".into());
        }
        if !ids.insert(operation.intent.request.command_id.clone()) {
            return Err("original application upgrade ID repeats".into());
        }
    }
    let authority: Option<ApplicationUpgradeAuthority> =
        read_meta::<Option<ApplicationUpgradeAuthority>>(connection, AUTHORITY_KEY)
            .map_err(|error| error.to_string())?
            .ok_or("original application upgrade authority head is absent")?;
    if let Some(authority) = &authority {
        authority.validate(original)?;
        validate_original_policy(connection, authority)?;
    }
    let member: crate::content_store::ReplicaContentIdentity =
        read_meta(connection, "content_root_identity")
            .map_err(|error| error.to_string())?
            .ok_or("upgrade storage lost its original member-local root identity")?;
    for operation in &operations {
        if operation
            .drain
            .as_ref()
            .is_some_and(|drain| drain.member != member.member)
        {
            return Err("local upgrade wait belongs to another original member".into());
        }
        if let Some(receipt) = authority.as_ref().and_then(|authority| {
            authority.receipts.iter().find(|receipt| {
                receipt.intent.request.command_id == operation.intent.request.command_id
            })
        }) && (receipt.intent != operation.intent
            || receipt
                .drains
                .iter()
                .find(|drain| drain.member == member.member)
                != operation.drain.as_ref())
        {
            return Err("activated upgrade differs from its original local intent/wait".into());
        }
        if authority.as_ref().is_some_and(|authority| {
            authority.aborts.iter().any(|abort| {
                abort.intent.request.command_id == operation.intent.request.command_id
                    && abort.intent != operation.intent
            })
        }) {
            return Err("abandoned upgrade differs from its original local intent".into());
        }
    }
    Ok(())
}

/// Requires the exact original policy for same-schema patches and refuses restoration of original code policy.
pub(crate) fn validate_original_policy(
    connection: &Connection,
    authority: &ApplicationUpgradeAuthority,
) -> Result<(), String> {
    let original: Option<String> =
        read_meta(connection, ORIGINAL_POLICY_KEY).map_err(|error| error.to_string())?;
    if original
        .as_ref()
        .is_some_and(|policy| !crate::content::hex_digest(policy))
    {
        return Err("original application policy witness is incomplete".into());
    }
    for receipt in &authority.receipts {
        if original
            .as_ref()
            .is_some_and(|policy| policy == &receipt.intent.request.target.policy_digest)
            || (receipt.intent.request.from_schema
                == receipt.intent.request.target.application_schema
                && original.is_none())
        {
            return Err(
                "same-schema upgrade lacks original policy or restores retired original code"
                    .into(),
            );
        }
    }
    Ok(())
}

impl SqliteRaftStore {
    /// Records only the actual startup policy already committed to the immutable original profile.
    pub(crate) fn bind_original_application_policy(&self, policy: &str) -> Result<(), String> {
        if !crate::content::hex_digest(policy) {
            return Err("original application policy is incomplete".into());
        }
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        if let Some(original) = read_meta::<String>(&transaction, ORIGINAL_POLICY_KEY)
            .map_err(|error| error.to_string())?
        {
            if original != policy {
                return Err("original application policy cannot be replaced".into());
            }
        } else {
            write_meta(&transaction, ORIGINAL_POLICY_KEY, &policy)
                .map_err(|error| error.to_string())?;
        }
        transaction.commit().map_err(|error| error.to_string())
    }
    /// Refuses a no-op, a previously activated executable and the original deployment policy before drain.
    pub(crate) fn require_new_application_binary(
        &self,
        target: &UpgradeBinary,
        authority: Option<&ApplicationUpgradeAuthority>,
    ) -> Result<(), String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let original: Option<String> =
            read_meta(&connection, ORIGINAL_POLICY_KEY).map_err(|error| error.to_string())?;
        let identity: (String, String) = read_meta(&connection, "identity")
            .map_err(|error| error.to_string())?
            .ok_or("original Replica identity is absent")?;
        let active = authority.map_or(
            identity.1.as_str(),
            ApplicationUpgradeAuthority::active_schema,
        );
        if (target.application_schema == active && original.is_none())
            || original.as_ref() == Some(&target.policy_digest)
            || authority.is_some_and(|authority| {
                authority.receipts.iter().any(|receipt| {
                    receipt.intent.request.target.binary_digest == target.binary_digest
                })
            })
        {
            return Err(
                "application upgrade cannot restore original or retired executable bytes".into(),
            );
        }
        Ok(())
    }
    /// Reads one exact original operational issue without changing its receive clock or outcome.
    pub(crate) fn local_application_upgrade(
        &self,
        command: &str,
    ) -> Result<Option<LocalUpgradeOperation>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let operations: Vec<LocalUpgradeOperation> = read_meta(&connection, LOCAL_KEY)
            .map_err(|error| error.to_string())?
            .unwrap_or_default();
        Ok(operations
            .into_iter()
            .find(|operation| operation.intent.request.command_id == command))
    }
    /// Lists only retained unresolved IDs; missing activation never asserts that a stop had no effect.
    pub(crate) fn pending_application_upgrade_ids(&self) -> Result<Vec<String>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let operations: Vec<LocalUpgradeOperation> = read_meta(&connection, LOCAL_KEY)
            .map_err(|error| error.to_string())?
            .unwrap_or_default();
        let authority: Option<ApplicationUpgradeAuthority> =
            read_meta::<Option<ApplicationUpgradeAuthority>>(&connection, AUTHORITY_KEY)
                .map_err(|error| error.to_string())?
                .flatten();
        Ok(operations
            .into_iter()
            .filter(|operation| !resolved(operation, authority.as_ref()))
            .map(|operation| operation.intent.request.command_id)
            .collect())
    }
    /// Reads committed writer authority without creating a new operation or renewing any budget.
    pub fn application_upgrade_authority(
        &self,
    ) -> Result<Option<ApplicationUpgradeAuthority>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        Ok(
            read_meta::<Option<ApplicationUpgradeAuthority>>(&connection, AUTHORITY_KEY)
                .map_err(|error| error.to_string())?
                .flatten(),
        )
    }
    /// Reports original unresolved local intents; expiry and lost acknowledgements never release the fence.
    pub(crate) fn application_upgrade_blocks(&self) -> Result<bool, String> {
        if self.controller_content {
            return self.read_network_snapshot(Self::upgrade_blocks);
        }
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        Self::upgrade_blocks(&connection)
    }
    /// Checks the original same-writer operational fence during spawn and application admission.
    pub(crate) fn upgrade_blocks(connection: &Connection) -> Result<bool, String> {
        let operations: Vec<LocalUpgradeOperation> = read_meta(connection, LOCAL_KEY)
            .map_err(|error| error.to_string())?
            .unwrap_or_default();
        let authority: Option<ApplicationUpgradeAuthority> =
            read_meta::<Option<ApplicationUpgradeAuthority>>(connection, AUTHORITY_KEY)
                .map_err(|error| error.to_string())?
                .flatten();
        Ok(operations
            .iter()
            .any(|operation| !resolved(operation, authority.as_ref())))
    }
    /// Reads the exact current writer schema while retaining the immutable original profile identity.
    pub(crate) fn application_writer_schema(
        &self,
        connection: &Connection,
    ) -> Result<String, String> {
        let identity: (String, String) = read_meta(connection, "identity")
            .map_err(|error| error.to_string())?
            .ok_or("original Replica identity is absent")?;
        let authority: Option<ApplicationUpgradeAuthority> =
            read_meta::<Option<ApplicationUpgradeAuthority>>(connection, AUTHORITY_KEY)
                .map_err(|error| error.to_string())?
                .flatten();
        if let Some(authority) = authority {
            authority.validate(&identity.1)?;
            Ok(authority.active_schema().into())
        } else {
            Ok(identity.1)
        }
    }
    /// Durably freezes one actual local original child before any stop request or acknowledgement.
    pub(crate) fn admit_application_upgrade(
        &self,
        intent: ApplicationUpgradeIntent,
        clock: ContentBudget,
        witness: &crate::tls::VerifiedTlsConnection,
        local_member: u64,
        supervisor_instance: &str,
    ) -> Result<LocalUpgradeOperation, String> {
        intent.validate()?;
        clock.check()?;
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let trust: CertificateTrust = read_meta(&transaction, "certificate_trust")
            .map_err(|error| error.to_string())?
            .ok_or("original upgrade TLS authority is absent")?;
        witness.authorize_with_trust(intent.epoch.replica_id, local_member, &trust)?;
        let membership: StoredMembership<u64, ReplicaMember> =
            read_meta(&transaction, "membership")
                .map_err(|error| error.to_string())?
                .unwrap_or_default();
        if self
            .accepted_identity_with_connection(&transaction)
            .map_err(|error| error.to_string())?
            != intent.predecessor
            || membership != intent.membership
        {
            return Err(
                "original upgrade application predecessor/membership changed under writer".into(),
            );
        }
        let mut operations: Vec<LocalUpgradeOperation> = read_meta(&transaction, LOCAL_KEY)
            .map_err(|error| error.to_string())?
            .unwrap_or_default();
        if let Some(original) = operations
            .iter()
            .find(|operation| operation.intent.request.command_id == intent.request.command_id)
        {
            if original.intent != intent {
                return Err(
                    "original application upgrade ID was reused with another intent".into(),
                );
            }
            return Ok(original.clone());
        }
        let authority: Option<ApplicationUpgradeAuthority> =
            read_meta::<Option<ApplicationUpgradeAuthority>>(&transaction, AUTHORITY_KEY)
                .map_err(|error| error.to_string())?
                .flatten();
        if authority.as_ref().is_some_and(|authority| {
            authority
                .aborts
                .iter()
                .any(|abort| abort.intent.request.command_id == intent.request.command_id)
        }) {
            return Err("original application upgrade was irreversibly abandoned".into());
        }
        if Self::upgrade_blocks(&transaction)?
            || operations.len() >= MAX_OPERATIONS
            || self.application_writer_schema(&transaction)? != intent.request.from_schema
        {
            return Err("application upgrade has another unresolved operation or writer".into());
        }
        clock.check()?;
        if clock.duration_ms > intent.request.budget_ms {
            return Err("original receiver upgrade budget exceeds original request".into());
        }
        install_format(&transaction)?;
        let (process, process_history_head) =
            crate::supervision::freeze_upgrade_process(&transaction, intent.epoch)?;
        let operation = LocalUpgradeOperation {
            intent,
            process,
            process_history_head,
            supervisor_instance: supervisor_instance.into(),
            clock,
            drain: None,
        };
        operations.push(operation.clone());
        write_meta(&transaction, LOCAL_KEY, &operations).map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(operation)
    }
    /// Independently retains actual original wait only; observing it never repeats the stop or activation.
    pub(crate) fn observe_application_upgrade_drain(
        &self,
        command: &str,
        member: u64,
        leaf: &str,
        supervisor: &str,
    ) -> Result<Option<ApplicationUpgradeDrain>, String> {
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let mut operations: Vec<LocalUpgradeOperation> = read_meta(&transaction, LOCAL_KEY)
            .map_err(|error| error.to_string())?
            .ok_or("original application upgrade operation is absent")?;
        let operation = operations
            .iter_mut()
            .find(|operation| operation.intent.request.command_id == command)
            .ok_or("original application upgrade ID is absent")?;
        if let Some(drain) = &operation.drain {
            return Ok(Some(drain.clone()));
        }
        // Drop the SQL writer before reading the independent original process journal on this same store.
        let original = operation.clone();
        drop(transaction);
        drop(connection);
        let _observer = supervisor;
        let Some(proof) = self.controller_drain_proof(
            original.intent.epoch,
            original.process.as_ref(),
            &original.supervisor_instance,
        )?
        else {
            return Ok(None);
        };
        let drain = ApplicationUpgradeDrain {
            member,
            leaf: leaf.into(),
            intent_digest: intent_digest(&original.intent),
            proof,
        };
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let mut operations: Vec<LocalUpgradeOperation> = read_meta(&transaction, LOCAL_KEY)
            .map_err(|error| error.to_string())?
            .ok_or("original upgrade history disappeared")?;
        let operation = operations
            .iter_mut()
            .find(|operation| operation.intent == original.intent)
            .ok_or("original upgrade intent changed")?;
        operation.drain = Some(drain.clone());
        write_meta(&transaction, LOCAL_KEY, &operations).map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(Some(drain))
    }
    /// Applies only a complete exact original activation beside the unchanged complete application head.
    pub(crate) fn apply_application_upgrade(
        &self,
        connection: &Connection,
        receipt: ApplicationUpgradeReceipt,
    ) -> Result<CommitReceipt, openraft::StorageError<u64>> {
        let original: (String, String) = read_meta(connection, "identity")?
            .ok_or_else(|| io_error("original Replica identity is absent"))?;
        let mut authority =
            read_meta::<Option<ApplicationUpgradeAuthority>>(connection, AUTHORITY_KEY)?
                .flatten()
                .unwrap_or(ApplicationUpgradeAuthority {
                    schema: "roboguide.application-upgrade-authority/v0.1".into(),
                    original_schema: original.1,
                    receipts: Vec::new(),
                    aborts: Vec::new(),
                });
        if let Some(existing) = authority.receipts.iter().find(|existing| {
            existing.intent.request.command_id == receipt.intent.request.command_id
        }) {
            return Ok(CommitReceipt {
                accepted: existing == &receipt,
                rejection: (existing != &receipt)
                    .then(|| "original application upgrade activation differs".into()),
                ..CommitReceipt::default()
            });
        }
        let current = self.accepted_identity_with_connection(connection)?;
        let membership: StoredMembership<u64, ReplicaMember> =
            read_meta(connection, "membership")?.unwrap_or_default();
        let trust: CertificateTrust = read_meta(connection, "certificate_trust")?
            .ok_or_else(|| io_error("original activation TLS authority is absent"))?;
        let applied = if current != receipt.intent.predecessor
            || membership != receipt.intent.membership
            || receipt
                .intent
                .supports
                .iter()
                .any(|proof| !trust.admits(proof.member, &proof.leaf))
        {
            Err(
                "application upgrade original predecessor, membership or peer permission changed"
                    .into(),
            )
        } else {
            authority.receipts.push(receipt);
            authority
                .validate(&authority.original_schema)
                .and_then(|()| validate_original_policy(connection, &authority))
        };
        if let Err(error) = applied {
            return Ok(CommitReceipt {
                rejection: Some(error),
                ..CommitReceipt::default()
            });
        }
        install_format(connection).map_err(io_error)?;
        write_meta(connection, AUTHORITY_KEY, &Some(authority))?;
        Ok(CommitReceipt {
            accepted: true,
            ..CommitReceipt::default()
        })
    }
    /// Orders abandonment against activation; it reduces permission and never releases an unreaped child.
    pub(crate) fn apply_application_upgrade_abort(
        &self,
        connection: &Connection,
        abort: ApplicationUpgradeAbort,
    ) -> Result<CommitReceipt, openraft::StorageError<u64>> {
        let original: (String, String) = read_meta(connection, "identity")?
            .ok_or_else(|| io_error("original Replica identity is absent"))?;
        let mut authority =
            read_meta::<Option<ApplicationUpgradeAuthority>>(connection, AUTHORITY_KEY)?
                .flatten()
                .unwrap_or(ApplicationUpgradeAuthority {
                    schema: "roboguide.application-upgrade-authority/v0.1".into(),
                    original_schema: original.1,
                    receipts: Vec::new(),
                    aborts: Vec::new(),
                });
        if let Some(existing) = authority
            .aborts
            .iter()
            .find(|value| value.command_id == abort.command_id)
        {
            return Ok(CommitReceipt {
                accepted: existing == &abort,
                rejection: (existing != &abort).then(|| "original abandonment ID differs".into()),
                ..CommitReceipt::default()
            });
        }
        if authority
            .receipts
            .iter()
            .any(|receipt| receipt.intent.request.command_id == abort.intent.request.command_id)
            || read_meta::<StoredMembership<u64, ReplicaMember>>(connection, "membership")?.as_ref()
                != Some(&abort.membership)
        {
            return Ok(CommitReceipt {
                rejection: Some(
                    "activated upgrade cannot be abandoned or original membership differs".into(),
                ),
                ..CommitReceipt::default()
            });
        }
        authority.aborts.push(abort);
        if let Err(error) = authority.validate(&authority.original_schema) {
            return Ok(CommitReceipt {
                rejection: Some(error),
                ..CommitReceipt::default()
            });
        }
        install_format(connection).map_err(io_error)?;
        write_meta(connection, AUTHORITY_KEY, &Some(authority))?;
        Ok(CommitReceipt {
            accepted: true,
            ..CommitReceipt::default()
        })
    }
    /// Reads one original atomic application predecessor without taking another store mutex.
    pub(crate) fn accepted_identity_with_connection(
        &self,
        connection: &Connection,
    ) -> Result<AcceptedApplicationIdentity, openraft::StorageError<u64>> {
        let checkpoint: Option<state::PersistedCheckpoint> = read_meta(connection, "checkpoint")?;
        Ok(AcceptedApplicationIdentity {
            revision: read_meta(connection, "revision")?.unwrap_or(0),
            event_sequence: checkpoint.as_ref().map_or(0, |value| value.event_sequence),
            checkpoint_digest: checkpoint.as_ref().map(state::checkpoint_identity),
        })
    }
}

/// Abandonment closes a local hold only after its own original independent wait has survived.
fn resolved(
    operation: &LocalUpgradeOperation,
    authority: Option<&ApplicationUpgradeAuthority>,
) -> bool {
    authority.is_some_and(|authority| {
        authority
            .receipts
            .iter()
            .any(|receipt| receipt.intent == operation.intent)
            || (operation.drain.is_some()
                && authority
                    .aborts
                    .iter()
                    .any(|abort| abort.intent == operation.intent))
    })
}

#[cfg(test)]
#[path = "upgrade_tests.rs"]
mod tests;
