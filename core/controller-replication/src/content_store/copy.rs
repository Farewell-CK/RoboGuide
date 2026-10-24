//! Actual original source copy; intent and independent receive pin precede the first payload read.

use super::time::ContentBudget;
use super::*;
use artifact_store::FileSystemArtifactStore;
use sha2::{Digest as _, Sha256};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, PermissionsExt};

/// Maximum one copied object, separate from application resources and semantic use budgets.
const MAX_COPY_BYTES: u64 = 1024 * 1024 * 1024;
/// Maximum working chunk while actual original clocks and writer identity are rechecked.
const CHUNK: usize = 64 * 1024;

/// Complete actual retained source writer observed before any source payload is opened.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceWriter {
    /// Original actual backend owner.
    owner: String,
    /// Actual original held source writer device.
    device: u64,
    /// Actual original held source writer inode.
    inode: u64,
}

/// Original command and nonrenewing effect budget, retained independently of copy outcome.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CopyIntent {
    /// Explicit interpretation; an opaque JSON claim never grants a copy.
    schema: String,
    /// Exact original receiving operation.
    command_id: String,
    /// Actual target software owner/member/profile.
    receiver: ReplicaContentIdentity,
    /// Exact immutable source byte identity.
    content: ArtifactContentKey,
    /// Complete actually held original source writer.
    source: SourceWriter,
    /// Original log/Snapshot/application producer identity.
    authority_digest: String,
    /// Actual original receiver issue time and process.
    budget: ContentBudget,
}

/// Actual member-local durable byte receipt; it never asserts a current quorum, Task or use grant.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentReceipt {
    /// Exact software storage receipt contract.
    pub schema: String,
    /// Complete original input/command identity.
    pub request_digest: String,
    /// Exact original operation ID.
    pub command_id: String,
    /// Original content remains independent of target identity.
    pub content: ArtifactContentKey,
    /// Actual original receiver software owner.
    pub store_owner: String,
    /// Actual configured infrastructure member.
    pub member: u64,
    /// Original immutable cluster/profile.
    pub profile_identity: String,
    /// Actual final regular CAS file device, not a device/robot identity.
    pub file_device: u64,
    /// Actual finalized file inode.
    pub file_inode: u64,
    /// Original same-kernel completion sample, never refreshed by retry.
    pub completed_ms: u64,
}

/// Original operation observation; absent receipt means Unknown, not unexecuted or unavailable bytes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentCopyStatus {
    /// Original operation identity.
    pub command_id: String,
    /// Exact original request digest.
    pub request_digest: String,
    /// Complete immutable original target.
    pub content: ArtifactContentKey,
    /// Original actual copy receipt, without reasserting current availability.
    pub receipt: Option<ReplicaContentReceipt>,
    /// Separate original-process absence observation; Unknown never becomes copied/Completed.
    pub process_end: Option<ContentProcessEndObservation>,
}

/// Complete bounded original copy row; no error path discards the initial intent or pin.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CopyRecord {
    /// Original first admitted request.
    intent: CopyIntent,
    /// Actual original completion, absent while uncertain.
    receipt: Option<ReplicaContentReceipt>,
    /// Independent end-of-copier evidence under the original actual root/database leases.
    process_end: Option<ContentProcessEndObservation>,
}

/// Uses canonical object ordering for exact immutable input and original qualified keys.
pub(super) fn digest(value: &impl Serialize) -> Result<String, String> {
    let raw = serde_json::to_vec(&serde_json::to_value(value).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    Ok(format!("{:x}", Sha256::digest(raw)))
}

/// Derives a path-safe original operation identity, never a caller-selected file path.
fn operation_key(command_id: &str) -> Result<String, String> {
    digest(&command_id)
}

/// Derives the immutable original receive pin independently of another producer's ownership.
fn receive_reference(intent: &CopyIntent) -> Result<ReplicaContentReference, String> {
    Ok(ReplicaContentReference {
        reference_id: format!("receive:{}", operation_key(&intent.command_id)?),
        kind: ReplicaContentReferenceKind::Receive,
        content: intent.content.clone(),
        authority_digest: intent.authority_digest.clone(),
        generation: 1,
    })
}

/// Resolves only an original qualified namespace under the actual member root.
pub(super) fn object_path(root: &Path, content: &ArtifactContentKey) -> PathBuf {
    root.join("content-v1")
        .join(&content.source_owner[15..])
        .join(&content.content_digest[7..])
}

/// Returns immutable original history; it neither repeats a copy nor grants current availability.
fn status(record: &CopyRecord) -> Result<ReplicaContentCopyStatus, String> {
    Ok(ReplicaContentCopyStatus {
        command_id: record.intent.command_id.clone(),
        request_digest: digest(&record.intent)?,
        content: record.intent.content.clone(),
        receipt: record.receipt.clone(),
        process_end: record.process_end.clone(),
    })
}

/// Reads one complete bounded original row without interpreting loss as permission to repeat.
fn operation(connection: &Connection, id: &str) -> Result<Option<CopyRecord>, String> {
    use rusqlite::OptionalExtension;
    let raw: Option<String> = connection
        .query_row(
            "SELECT record FROM content_operations WHERE operation_id=?",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    raw.map(|raw| {
        if raw.len() > 16 * 1024 {
            return Err("original content copy record exceeds bound".into());
        }
        serde_json::from_str(&raw).map_err(|error| error.to_string())
    })
    .transpose()
}

/// Checks retained original copy evidence even after current availability or unrelated pins have retired.
pub(super) fn validate_original_receipt(
    connection: &Connection,
    receipt: &ReplicaContentReceipt,
) -> Result<(), String> {
    let original = operation(connection, &operation_key(&receipt.command_id)?)?
        .ok_or("original byte receipt lost its copy operation")?;
    if original.receipt.as_ref() != Some(receipt) {
        return Err("original byte receipt changed its copy evidence".into());
    }
    Ok(())
}

/// Verifies every retained original operation/object against its exact immutable source and pin.
pub(super) fn validate_records(
    connection: &Connection,
    identity: &ReplicaContentIdentity,
) -> Result<(), String> {
    let operations = layout::head(connection, "operation_ids")?;
    for id in &operations {
        let record =
            operation(connection, id)?.ok_or("original content operation head lost its row")?;
        let intent = &record.intent;
        intent.content.validate()?;
        intent.budget.validate()?;
        if intent.schema != "roboguide.replica-content-copy/v0.1"
            || intent.receiver != *identity
            || intent.command_id.is_empty()
            || intent.command_id.len() > 128
            || operation_key(&intent.command_id)? != *id
            || intent.source.owner != intent.content.source_owner
            || intent.source.inode == 0
            || intent.content.byte_size > MAX_COPY_BYTES
            || !crate::content::hex_digest(&intent.authority_digest)
        {
            return Err("original content operation source/owner/budget differs".into());
        }
        let expected = receive_reference(intent)?;
        let pin = reference_record(connection, &expected.reference_id)?
            .ok_or("original receive intent lost its independent pin")?;
        if pin.reference != expected
            || (record.receipt.is_none() && record.process_end.is_none() && !pin.active)
            || (record.receipt.is_some() && record.process_end.is_some())
            || (record.process_end.is_some()
                && (pin.active || pin.release_digest.as_deref() != Some(digest(&record)?.as_str())))
        {
            return Err("original incomplete receive lost its exact retained pin".into());
        }
        if record
            .process_end
            .as_ref()
            .is_some_and(|end| !intent.budget.validates_end(end))
        {
            return Err("original receive process-end evidence changed incarnation".into());
        }
        if let Some(receipt) = &record.receipt
            && (receipt.schema != "roboguide.replica-content-receipt/v0.1"
                || receipt.request_digest != digest(intent)?
                || receipt.command_id != intent.command_id
                || receipt.content != intent.content
                || receipt.store_owner != identity.store_owner
                || receipt.member != identity.member
                || receipt.profile_identity != identity.profile_identity
                || receipt.file_inode == 0
                || !intent.budget.validates_completion(receipt.completed_ms))
        {
            return Err(
                "original content byte receipt lacks its exact original completion evidence".into(),
            );
        }
    }
    for id in layout::head(connection, "reference_ids")? {
        let pin = reference_record(connection, &id)?.ok_or("original content pin disappeared")?;
        if pin.reference.kind == ReplicaContentReferenceKind::Receive {
            let operation_id = id
                .strip_prefix("receive:")
                .ok_or("original receive pin has another owner")?;
            if !operations.contains(operation_id) {
                return Err("original receive pin lost its operation authority".into());
            }
        }
    }
    for key in layout::head(connection, "content_keys")? {
        let raw: String = connection
            .query_row(
                "SELECT record FROM content_objects WHERE content_key=?",
                [&key],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if raw.len() > 16 * 1024 {
            return Err("original content object record exceeds bound".into());
        }
        let receipt: ReplicaContentReceipt =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        if digest(&receipt.content)? != key {
            return Err("original content object key changed source identity".into());
        }
        if receipt.schema == super::streams::RECEIPT_SCHEMA {
            let version: i64 = connection
                .query_row("PRAGMA user_version", [], |row| row.get(0))
                .map_err(|error| error.to_string())?;
            if !matches!(version, 4..=7) {
                return Err("legacy object profile contains a hidden stream receipt".into());
            }
            super::streams::validate_object_receipt(connection, &receipt)?;
            continue;
        }
        let original = operation(connection, &operation_key(&receipt.command_id)?)?
            .ok_or("original content object lost its copy authority")?;
        if original.receipt.as_ref() != Some(&receipt) {
            return Err("original content object differs from actual original copy receipt".into());
        }
    }
    Ok(())
}

/// Refuses generic release of an unresolved receiver; it needs its own actual drain/abort authority.
pub(super) fn release_allowed(
    connection: &Connection,
    reference: &ReplicaContentReference,
) -> Result<(), String> {
    if reference.kind != ReplicaContentReferenceKind::Receive {
        return Ok(());
    }
    let id = reference
        .reference_id
        .strip_prefix("receive:")
        .ok_or("original receiving owner is unavailable")?;
    if operation(connection, id)?.is_none_or(|record| record.receipt.is_none()) {
        return Err("original uncertain receiving operation still retains its pin".into());
    }
    Ok(())
}

/// Reads and hashes the actual qualified file; original command history alone cannot prove availability.
pub(super) fn verify_file(path: &Path, content: &ArtifactContentKey) -> Result<File, String> {
    verify_file_with_budget(path, content, None)
}

/// Hashes actual bytes within an original reader budget and returns the original handle at offset zero.
pub(super) fn verify_file_with_budget(
    path: &Path,
    content: &ArtifactContentKey,
    budget: Option<&ContentBudget>,
) -> Result<File, String> {
    verify_file_with_check(path, content, || {
        if let Some(budget) = budget {
            budget.check()?;
        }
        Ok(())
    })
}

/// Checks original live authority before and after every actual bounded verification read.
pub(super) fn verify_file_with_check(
    path: &Path,
    content: &ArtifactContentKey,
    check: impl Fn() -> Result<(), String>,
) -> Result<File, String> {
    use std::io::{Seek, SeekFrom};
    check()?;
    let file = layout::regular(path, false)?;
    if file.metadata().map_err(|error| error.to_string())?.len() != content.byte_size {
        return Err("actual replica content byte count differs".into());
    }
    let mut observed = file.try_clone().map_err(|error| error.to_string())?;
    let mut hash = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = [0_u8; CHUNK];
    loop {
        check()?;
        let count = observed
            .read(&mut buffer)
            .map_err(|error| error.to_string())?;
        check()?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(count as u64)
            .ok_or("replica content size overflow")?;
        if total > content.byte_size {
            return Err("actual replica content grew beyond original size".into());
        }
        hash.update(&buffer[..count]);
    }
    if total != content.byte_size
        || format!("sha256:{:x}", hash.finalize()) != content.content_digest
    {
        return Err("actual replica content hash differs".into());
    }
    observed
        .seek(SeekFrom::Start(0))
        .map_err(|error| error.to_string())?;
    check()?;
    Ok(file)
}

impl ReplicaContentStore {
    /// Persists original intent/pin, then actually copies and fsyncs bytes from an owned Artifact store.
    /// A repeated original ID only returns its original status, including uncertain partial I/O.
    pub fn copy_from_artifact(
        &self,
        source: &FileSystemArtifactStore,
        content: &ArtifactContentKey,
        command_id: &str,
        authority_digest: &str,
        duration_ms: u64,
    ) -> Result<ReplicaContentCopyStatus, String> {
        content.validate()?;
        if command_id.is_empty()
            || command_id.len() > 128
            || !crate::content::hex_digest(authority_digest)
            || content.byte_size > MAX_COPY_BYTES
        {
            return Err("invalid bounded original content copy input".into());
        }
        let budget = ContentBudget::issue(duration_ms)?;
        let source_writer = source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        if source_writer.owner_id != content.source_owner {
            return Err("actual source writer owns another original Artifact namespace".into());
        }
        let source_identity = SourceWriter {
            owner: source_writer.owner_id,
            device: source_writer.lease_device,
            inode: source_writer.lease_inode,
        };
        let operation_id = operation_key(command_id)?;
        let intent = CopyIntent {
            schema: "roboguide.replica-content-copy/v0.1".into(),
            command_id: command_id.into(),
            receiver: self.identity().clone(),
            content: content.clone(),
            source: source_identity.clone(),
            authority_digest: authority_digest.into(),
            budget,
        };
        let reference = receive_reference(&intent)?;
        {
            let mut guard = self
                .inner
                .connection
                .lock()
                .map_err(|error| error.to_string())?;
            let connection = guard
                .as_mut()
                .ok_or("original content connection is closed")?;
            self.validate_connection(connection)?;
            if let Some(original) = operation(connection, &operation_id)? {
                if original.intent.content != *content
                    || original.intent.source != source_identity
                    || original.intent.authority_digest != authority_digest
                    || original.intent.budget.duration_ms != duration_ms
                {
                    return Err("original content copy ID has different immutable input".into());
                }
                return status(&original);
            }
            super::gc::require_live(connection, content)?;
            intent.budget.check()?;
            let transaction = connection
                .transaction()
                .map_err(|error| error.to_string())?;
            let mut commands = layout::head(&transaction, "operation_ids")?;
            if commands.len() >= 4096 {
                return Err("original content copy history reached its bound".into());
            }
            let mut references = layout::head(&transaction, "reference_ids")?;
            let record = CopyRecord {
                intent: intent.clone(),
                receipt: None,
                process_end: None,
            };
            transaction
                .execute(
                    "INSERT INTO content_operations VALUES (?1,?2)",
                    params![
                        operation_id,
                        serde_json::to_string(&record).map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            transaction
                .execute(
                    "INSERT INTO content_references VALUES (?1,?2)",
                    params![
                        reference.reference_id,
                        serde_json::to_string(&ReferenceRecord {
                            reference: reference.clone(),
                            active: true,
                            release_digest: None
                        })
                        .map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            commands.insert(operation_id.clone());
            references.insert(reference.reference_id.clone());
            layout::write_head(&transaction, "operation_ids", &commands)?;
            layout::write_head(&transaction, "reference_ids", &references)?;
            layout::within_budget(&transaction)?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        intent.budget.check()?;
        let destination = object_path(self.root(), content);
        if !destination.exists() {
            let staging = self.root().join("staging-v1").join(&operation_id);
            let mut output = layout::new_file(&staging)?;
            let mut input = source
                .open_artifact(&content.content_digest)
                .map_err(|error| error.to_string())?;
            let mut hash = Sha256::new();
            let mut total = 0_u64;
            let mut buffer = [0_u8; CHUNK];
            loop {
                intent.budget.check()?;
                let current = source
                    .writer_identity()
                    .map_err(|error| error.to_string())?;
                if (
                    current.owner_id.as_str(),
                    current.lease_device,
                    current.lease_inode,
                ) != (
                    source_identity.owner.as_str(),
                    source_identity.device,
                    source_identity.inode,
                ) {
                    return Err("original source writer changed during content copy".into());
                }
                let count = input.read(&mut buffer).map_err(|error| error.to_string())?;
                if count == 0 {
                    break;
                }
                total = total
                    .checked_add(count as u64)
                    .ok_or("original content copy size overflow")?;
                if total > content.byte_size {
                    return Err("original source content exceeds its exact size".into());
                }
                hash.update(&buffer[..count]);
                output
                    .write_all(&buffer[..count])
                    .map_err(|error| error.to_string())?;
            }
            if total != content.byte_size
                || format!("sha256:{:x}", hash.finalize()) != content.content_digest
            {
                return Err("actual original source bytes differ from the admitted content".into());
            }
            output.sync_all().map_err(|error| error.to_string())?;
            output
                .set_permissions(std::fs::Permissions::from_mode(0o400))
                .and_then(|()| output.sync_all())
                .map_err(|error| error.to_string())?;
            intent.budget.check()?;
            let parent = destination
                .parent()
                .ok_or("qualified content parent unavailable")?;
            match std::fs::create_dir(parent) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error.to_string()),
            }
            layout::directory(parent)?;
            match std::fs::hard_link(&staging, &destination) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error.to_string()),
            }
            File::open(parent)
                .and_then(|file| file.sync_all())
                .map_err(|error| error.to_string())?;
            File::open(
                parent
                    .parent()
                    .ok_or("content namespace parent unavailable")?,
            )
            .and_then(|file| file.sync_all())
            .map_err(|error| error.to_string())?;
        }
        let file = verify_file_with_budget(&destination, content, Some(&intent.budget))?;
        file.sync_all().map_err(|error| error.to_string())?;
        let actual = file.metadata().map_err(|error| error.to_string())?;
        let receipt = ReplicaContentReceipt {
            schema: "roboguide.replica-content-receipt/v0.1".into(),
            request_digest: digest(&intent)?,
            command_id: command_id.into(),
            content: content.clone(),
            store_owner: self.identity().store_owner.clone(),
            member: self.identity().member,
            profile_identity: self.identity().profile_identity.clone(),
            file_device: actual.dev(),
            file_inode: actual.ino(),
            completed_ms: intent.budget.check()?,
        };
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        intent.budget.check()?;
        let original = operation(&transaction, &operation_id)?
            .ok_or("original content copy intent disappeared")?;
        if original.intent != intent || original.receipt.is_some() {
            return Err("original content copy authority changed during I/O".into());
        }
        let record = CopyRecord {
            intent,
            receipt: Some(receipt.clone()),
            process_end: None,
        };
        transaction
            .execute(
                "UPDATE content_operations SET record=?2 WHERE operation_id=?1",
                params![
                    operation_id,
                    serde_json::to_string(&record).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        let mut objects = layout::head(&transaction, "content_keys")?;
        let key = digest(content)?;
        transaction.execute("INSERT INTO content_objects(content_key,record) VALUES (?1,?2) ON CONFLICT(content_key) DO NOTHING",params![key,serde_json::to_string(&receipt).map_err(|error|error.to_string())?]).map_err(|error|error.to_string())?;
        objects.insert(key);
        layout::write_head(&transaction, "content_keys", &objects)?;
        layout::within_budget(&transaction)?;
        record.intent.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        status(&record)
    }

    /// Reads immutable original outcome only; it never repeats source reads, copies or budgets.
    pub fn copy_status(
        &self,
        command_id: &str,
    ) -> Result<Option<ReplicaContentCopyStatus>, String> {
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        operation(connection, &operation_key(command_id)?)?
            .map(|record| status(&record))
            .transpose()
    }

    /// Checks original object metadata only; callers must separately hash actual bytes before availability.
    pub(super) fn verify_content_metadata(
        &self,
        content: &ArtifactContentKey,
    ) -> Result<(), String> {
        content.validate()?;
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        super::gc::require_live(connection, content)?;
        let raw: String = connection
            .query_row(
                "SELECT record FROM content_objects WHERE content_key=?",
                [digest(content)?],
                |row| row.get(0),
            )
            .map_err(|error| match error {
                rusqlite::Error::QueryReturnedNoRows => {
                    "original qualified content is unavailable in this receiving backend".into()
                }
                _ => error.to_string(),
            })?;
        let receipt: ReplicaContentReceipt =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        if receipt.content != *content || receipt.store_owner != self.identity().store_owner {
            return Err("original qualified content ownership differs".into());
        }
        Ok(())
    }

    /// Independently verifies actual current qualified CAS bytes rather than reusing a historical ACK.
    pub fn verify_content(&self, content: &ArtifactContentKey) -> Result<(), String> {
        self.verify_content_metadata(content)?;
        verify_file(&object_path(self.root(), content), content)?;
        Ok(())
    }

    /// Separately retires an unresolved receive only after actual original-process absence.
    /// It preserves original Unknown, partial files and budgets; no byte receipt is invented.
    pub fn retire_ended_copy(&self, command_id: &str) -> Result<ReplicaContentCopyStatus, String> {
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let id = operation_key(command_id)?;
        let mut original =
            operation(connection, &id)?.ok_or("original receive operation is unavailable")?;
        if original.receipt.is_some() || original.process_end.is_some() {
            return status(&original);
        }
        original.process_end = Some(original.intent.budget.observe_process_end()?);
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let expected = receive_reference(&original.intent)?;
        let mut pin = reference_record(&transaction, &expected.reference_id)?
            .ok_or("original receive pin disappeared")?;
        if pin.reference != expected || !pin.active {
            return Err("original receive pin changed before process-end retirement".into());
        }
        pin.active = false;
        pin.release_digest = Some(digest(&original)?);
        transaction
            .execute(
                "UPDATE content_operations SET record=?2 WHERE operation_id=?1",
                params![
                    id,
                    serde_json::to_string(&original).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        transaction
            .execute(
                "UPDATE content_references SET record=?2 WHERE reference_id=?1",
                params![
                    expected.reference_id,
                    serde_json::to_string(&pin).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        layout::within_budget(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        status(&original)
    }
}
