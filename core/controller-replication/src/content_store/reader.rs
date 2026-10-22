//! Actual managed readers retain original pins and writer leases until independently closed.

use super::copy::digest;
use super::time::ContentBudget;
use super::*;
use sha2::{Digest as _, Sha256};
use std::io::Read;
use std::os::unix::fs::MetadataExt;

/// Preserves original v0.1 canonical digest bytes when a legacy payload reader had no scope field.
fn is_false(value: &bool) -> bool {
    !*value
}

/// Complete original reader admission; no serialized field alone grants semantic consumption.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ReaderIntent {
    /// Explicit internal storage interpretation.
    pub(super) schema: String,
    /// Original one-shot producer reader identity.
    pub(super) reader_id: String,
    /// Actual original receiving content backend.
    pub(super) receiver: ReplicaContentIdentity,
    /// Full original qualified content.
    pub(super) content: ArtifactContentKey,
    /// Exact log/Snapshot/transport producer authorization.
    pub(super) authority_digest: String,
    /// Original actual process and nonrenewing read budget, admitted before queue/open.
    pub(super) budget: ContentBudget,
    /// Explicit infrastructure retention scope rather than an actual payload FD read.
    #[serde(default, skip_serializing_if = "is_false")]
    pub(super) retention_only: bool,
}

/// Actual original handle-close proof; closing a partial read does not prove complete bytes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentReaderReceipt {
    /// Explicit software reader receipt contract.
    pub schema: String,
    /// Exact immutable original reader admission.
    pub request_digest: String,
    /// Original reader operation identity.
    pub reader_id: String,
    /// Full original content identity.
    pub content: ArtifactContentKey,
    /// Original actual local storage owner.
    pub store_owner: String,
    /// Original opened regular file device.
    pub file_device: u64,
    /// Original opened regular file inode.
    pub file_inode: u64,
    /// Actual bytes returned through this original managed handle.
    pub bytes_read: u64,
    /// Hash of the actual returned sequence, including a partial/empty read.
    pub observed_digest: String,
    /// True only for the complete original size/hash with no observed I/O failure.
    pub complete: bool,
    /// Actual same-kernel sample after closing the original file descriptor.
    pub closed_ms: u64,
    /// Explicit retention-scope closure; no actual content bytes or file are claimed in this case.
    #[serde(default, skip_serializing_if = "is_false")]
    pub retention_only: bool,
}

/// Immutable original reader observation; an unresolved operation retains its independent pin.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentReaderStatus {
    /// Original operation identity.
    pub reader_id: String,
    /// Original complete admission identity.
    pub request_digest: String,
    /// Original full qualified content.
    pub content: ArtifactContentKey,
    /// Actual original handle-close receipt, unavailable after an interrupted/unrecorded close.
    pub receipt: Option<ReplicaContentReaderReceipt>,
    /// Separate actual original-process absence observation, never an invented read receipt.
    pub process_end: Option<ContentProcessEndObservation>,
}

/// Original operation retains separate close and post-exit observations; neither is renewed on retry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ReaderRecord {
    /// Original admission before any file payload is read.
    pub(super) intent: ReaderIntent,
    /// Actual original descriptor closure only.
    pub(super) receipt: Option<ReplicaContentReaderReceipt>,
    /// Separate original-process absence under all held content writer leases.
    pub(super) process_end: Option<ContentProcessEndObservation>,
}

/// Managed actual reader; dropping it closes its FD but deliberately retains unresolved durable pins.
pub struct ReplicaContentReader {
    /// Holds the actual root/database leases through this entire original handle lifetime.
    store: ReplicaContentStore,
    /// Complete original persisted admission.
    intent: ReaderIntent,
    /// Original actual verified file; never reopened on a duplicate command.
    file: Option<File>,
    /// Actual returned byte count, bounded by the complete original input.
    bytes_read: u64,
    /// Hash of actual bytes returned from this one original file descriptor.
    hash: Sha256,
    /// Any actual I/O failure prevents reporting complete-byte evidence.
    failed: bool,
    /// Actual opened original file identity retained even after descriptor close.
    file_identity: (u64, u64),
}

impl Drop for ReplicaContentReader {
    /// Closes the actual payload descriptor before its final shared root/SQL lease can be released.
    /// Unrecorded closure retains its original durable Unknown; Drop never writes a receipt.
    fn drop(&mut self) {
        drop(self.file.take());
    }
}

/// Derives a fixed path-safe original reader row identity independently of caller labels.
pub(super) fn key(reader_id: &str) -> Result<String, String> {
    digest(&reader_id)
}

/// Derives this actual reader's independent pin; Snapshot replacement cannot own its release.
pub(super) fn reference(intent: &ReaderIntent) -> Result<ReplicaContentReference, String> {
    Ok(ReplicaContentReference {
        reference_id: format!("reader:{}", key(&intent.reader_id)?),
        kind: ReplicaContentReferenceKind::Reader,
        content: intent.content.clone(),
        authority_digest: intent.authority_digest.clone(),
        generation: 1,
    })
}

/// Reads one bounded original operation without treating a missing row as an unused command.
pub(super) fn record(connection: &Connection, id: &str) -> Result<Option<ReaderRecord>, String> {
    use rusqlite::OptionalExtension;
    let raw: Option<String> = connection
        .query_row(
            "SELECT record FROM content_readers WHERE reader_id=?",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    raw.map(|raw| {
        if raw.len() > 24 * 1024 {
            return Err("original content reader record exceeds bound".into());
        }
        serde_json::from_str(&raw).map_err(|error| error.to_string())
    })
    .transpose()
}

/// Reports retained history without reopening a file or renewing its original budget.
fn status(original: &ReaderRecord) -> Result<ReplicaContentReaderStatus, String> {
    Ok(ReplicaContentReaderStatus {
        reader_id: original.intent.reader_id.clone(),
        request_digest: digest(&original.intent)?,
        content: original.intent.content.clone(),
        receipt: original.receipt.clone(),
        process_end: original.process_end.clone(),
    })
}

/// Persists this exact original reader outcome and pin release in the same FULL SQL transaction.
pub(super) fn publish(connection: &mut Connection, original: &ReaderRecord) -> Result<(), String> {
    let transaction = connection
        .transaction()
        .map_err(|error| error.to_string())?;
    let id = key(&original.intent.reader_id)?;
    let current = record(&transaction, &id)?.ok_or("original reader admission disappeared")?;
    if current.intent != original.intent
        || current.receipt.is_some()
        || current.process_end.is_some()
    {
        return Err("original reader outcome changed during I/O".into());
    }
    let expected = reference(&original.intent)?;
    let mut pin = reference_record(&transaction, &expected.reference_id)?
        .ok_or("original reader pin disappeared")?;
    if pin.reference != expected || !pin.active {
        return Err("original reader pin changed before close".into());
    }
    pin.active = false;
    pin.release_digest = Some(digest(original)?);
    transaction
        .execute(
            "UPDATE content_readers SET record=?2 WHERE reader_id=?1",
            params![
                id,
                serde_json::to_string(original).map_err(|error| error.to_string())?
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
    transaction.commit().map_err(|error| error.to_string())
}

/// Validates all mandatory reader history, exact pins and immutable original close/process evidence.
pub(super) fn validate_records(
    connection: &Connection,
    identity: &ReplicaContentIdentity,
) -> Result<(), String> {
    let readers = layout::head(connection, "reader_ids")?;
    for id in &readers {
        let original = record(connection, id)?.ok_or("original reader head lost its row")?;
        let intent = &original.intent;
        intent.content.validate()?;
        intent.budget.validate()?;
        if intent.schema != "roboguide.replica-content-reader/v0.1"
            || intent.receiver != *identity
            || intent.reader_id.is_empty()
            || intent.reader_id.len() > 128
            || key(&intent.reader_id)? != *id
            || !crate::content::hex_digest(&intent.authority_digest)
            || (original.receipt.is_some() && original.process_end.is_some())
        {
            return Err("original reader source/owner/operation differs".into());
        }
        let expected = reference(intent)?;
        let pin = reference_record(connection, &expected.reference_id)?
            .ok_or("original reader lost its independent pin")?;
        let ended = original.receipt.is_some() || original.process_end.is_some();
        if pin.reference != expected
            || pin.active == ended
            || (ended && pin.release_digest.as_deref() != Some(digest(&original)?.as_str()))
        {
            return Err("original reader release lost its actual original outcome".into());
        }
        if let Some(receipt) = &original.receipt {
            let complete = receipt.bytes_read == intent.content.byte_size
                && receipt.observed_digest == intent.content.content_digest;
            if receipt.schema != "roboguide.replica-content-reader-receipt/v0.1"
                || receipt.request_digest != digest(intent)?
                || receipt.reader_id != intent.reader_id
                || receipt.content != intent.content
                || receipt.store_owner != identity.store_owner
                || receipt.retention_only != intent.retention_only
                || (!intent.retention_only && receipt.file_inode == 0)
                || (intent.retention_only
                    && (receipt.file_device != 0
                        || receipt.file_inode != 0
                        || receipt.bytes_read != 0
                        || receipt.observed_digest != artifact_store::digest_bytes(b"")
                        || receipt.complete))
                || receipt.bytes_read > intent.content.byte_size
                || receipt
                    .observed_digest
                    .strip_prefix("sha256:")
                    .is_none_or(|value| !crate::content::hex_digest(value))
                || (receipt.complete
                    && (!complete || !intent.budget.validates_completion(receipt.closed_ms)))
                || !intent.budget.validates_close(receipt.closed_ms)
            {
                return Err("original reader close lacks exact original evidence".into());
            }
        }
        if original
            .process_end
            .as_ref()
            .is_some_and(|end| !intent.budget.validates_end(end))
        {
            return Err("original reader process-end evidence changed incarnation".into());
        }
    }
    for id in layout::head(connection, "reference_ids")? {
        let pin = reference_record(connection, &id)?.ok_or("original reader pin disappeared")?;
        if pin.reference.kind == ReplicaContentReferenceKind::Reader
            && !id
                .strip_prefix("reader:")
                .is_some_and(|id| readers.contains(id))
        {
            return Err("original reader pin lost its operation authority".into());
        }
    }
    Ok(())
}

impl ReplicaContentStore {
    /// Admits and pins one actual reader before opening/verifying payload bytes; original IDs never reopen.
    pub fn open_reader(
        &self,
        content: &ArtifactContentKey,
        reader_id: &str,
        authority_digest: &str,
        duration_ms: u64,
    ) -> Result<ReplicaContentReader, String> {
        content.validate()?;
        if reader_id.is_empty()
            || reader_id.len() > 128
            || !crate::content::hex_digest(authority_digest)
        {
            return Err("invalid bounded original content reader".into());
        }
        let intent = ReaderIntent {
            schema: "roboguide.replica-content-reader/v0.1".into(),
            reader_id: reader_id.into(),
            receiver: self.identity().clone(),
            content: content.clone(),
            authority_digest: authority_digest.into(),
            budget: ContentBudget::issue(duration_ms)?,
            retention_only: false,
        };
        let id = key(reader_id)?;
        let expected = reference(&intent)?;
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
            if record(connection, &id)?.is_some() {
                return Err(
                    "original reader ID is retained; observe it without reopening bytes".into(),
                );
            }
            intent.budget.check()?;
            let transaction = connection
                .transaction()
                .map_err(|error| error.to_string())?;
            let mut readers = layout::head(&transaction, "reader_ids")?;
            if readers.len() >= 4096 {
                return Err("original content reader history reached its fixed bound".into());
            }
            let mut pins = layout::head(&transaction, "reference_ids")?;
            transaction
                .execute(
                    "INSERT INTO content_readers VALUES (?1,?2)",
                    params![
                        id,
                        serde_json::to_string(&ReaderRecord {
                            intent: intent.clone(),
                            receipt: None,
                            process_end: None
                        })
                        .map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            transaction
                .execute(
                    "INSERT INTO content_references VALUES (?1,?2)",
                    params![
                        expected.reference_id,
                        serde_json::to_string(&ReferenceRecord {
                            reference: expected.clone(),
                            active: true,
                            release_digest: None
                        })
                        .map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            readers.insert(id);
            pins.insert(expected.reference_id);
            layout::write_head(&transaction, "reader_ids", &readers)?;
            layout::write_head(&transaction, "reference_ids", &pins)?;
            layout::within_budget(&transaction)?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        self.verify_content_metadata(content)?;
        let file = copy::verify_file_with_budget(
            &copy::object_path(self.root(), content),
            content,
            Some(&intent.budget),
        )?;
        let actual = file.metadata().map_err(|error| error.to_string())?;
        intent.budget.check()?;
        Ok(ReplicaContentReader {
            store: self.clone(),
            intent,
            file: Some(file),
            bytes_read: 0,
            hash: Sha256::new(),
            failed: false,
            file_identity: (actual.dev(), actual.ino()),
        })
    }

    /// Observes only the immutable original reader; no file is opened and no deadline changes.
    pub fn reader_status(
        &self,
        reader_id: &str,
    ) -> Result<Option<ReplicaContentReaderStatus>, String> {
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        record(connection, &key(reader_id)?)?
            .as_ref()
            .map(status)
            .transpose()
    }

    /// Retires only an unresolved original reader after independently observing its process is gone.
    /// This creates no read-completion, OS-wait, semantic-use or device-stop receipt.
    pub fn retire_ended_reader(
        &self,
        reader_id: &str,
    ) -> Result<ReplicaContentReaderStatus, String> {
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let mut original =
            record(connection, &key(reader_id)?)?.ok_or("original reader is unavailable")?;
        if original.receipt.is_some() || original.process_end.is_some() {
            return status(&original);
        }
        original.process_end = Some(original.intent.budget.observe_process_end()?);
        publish(connection, &original)?;
        status(&original)
    }
}

impl Read for ReplicaContentReader {
    /// Reads bounded original bytes only under the original kernel budget and actual FD identity.
    fn read(&mut self, output: &mut [u8]) -> std::io::Result<usize> {
        let result = (|| -> Result<usize, String> {
            self.intent.budget.check()?;
            let file = self
                .file
                .as_mut()
                .ok_or("original reader handle is closed")?;
            let metadata = file.metadata().map_err(|error| error.to_string())?;
            if (metadata.dev(), metadata.ino(), metadata.len())
                != (
                    self.file_identity.0,
                    self.file_identity.1,
                    self.intent.content.byte_size,
                )
            {
                return Err("actual original reader file identity/size changed".into());
            }
            let limit = output.len().min(64 * 1024);
            let count = file
                .read(&mut output[..limit])
                .map_err(|error| error.to_string())?;
            if self
                .bytes_read
                .checked_add(count as u64)
                .is_none_or(|size| size > self.intent.content.byte_size)
            {
                return Err("actual original reader exceeded exact content size".into());
            }
            self.bytes_read += count as u64;
            self.hash.update(&output[..count]);
            Ok(count)
        })();
        result.map_err(|error| {
            self.failed = true;
            std::io::Error::other(error)
        })
    }
}

impl ReplicaContentReader {
    /// Closes this actual original FD before atomically recording close proof and releasing its pin.
    /// SQL failure preserves Unknown and the original pin; Drop never fabricates a durable close.
    pub fn finish(mut self) -> Result<ReplicaContentReaderReceipt, String> {
        let file = self
            .file
            .take()
            .ok_or("original reader handle is already closed")?;
        drop(file);
        let observed_digest = format!("sha256:{:x}", self.hash.clone().finalize());
        let closed_ms = self.intent.budget.closed_now()?;
        let receipt = ReplicaContentReaderReceipt {
            schema: "roboguide.replica-content-reader-receipt/v0.1".into(),
            request_digest: digest(&self.intent)?,
            reader_id: self.intent.reader_id.clone(),
            content: self.intent.content.clone(),
            store_owner: self.store.identity().store_owner.clone(),
            file_device: self.file_identity.0,
            file_inode: self.file_identity.1,
            bytes_read: self.bytes_read,
            complete: !self.failed
                && self.bytes_read == self.intent.content.byte_size
                && observed_digest == self.intent.content.content_digest
                && self.intent.budget.validates_completion(closed_ms),
            observed_digest,
            closed_ms,
            retention_only: false,
        };
        let mut guard = self
            .store
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.store.validate_connection(connection)?;
        publish(
            connection,
            &ReaderRecord {
                intent: self.intent.clone(),
                receipt: Some(receipt.clone()),
                process_end: None,
            },
        )?;
        Ok(receipt)
    }
}
