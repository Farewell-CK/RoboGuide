//! Original scoped infrastructure readers retain independent pins even after Snapshot replacement.

use super::reader::{ReaderIntent, ReaderRecord, ReplicaContentReaderReceipt};
use super::time::ContentBudget;
use super::*;

/// Actual synchronous/asynchronous reader scope; Drop closes only its original retention ownership.
/// A process kill or failed SQL closure leaves Unknown, independently retired after actual process absence.
pub struct ReplicaContentReadLease {
    /// Actual original root/SQL leases retained through the complete scope.
    store: ReplicaContentStore,
    /// Complete original admitted scope per qualified input, each with its own durable pin.
    originals: Vec<ReaderIntent>,
}

impl ReplicaContentStore {
    /// Persists a complete original reader set before opening a Snapshot/transport scope.
    /// No byte is read here and no content/use/availability receipt is created.
    pub fn retain_reader_scope(
        &self,
        reader_id: &str,
        inputs: &[ArtifactContentKey],
        authority_digest: &str,
    ) -> Result<ReplicaContentReadLease, String> {
        if reader_id.is_empty()
            || reader_id.len() > 64
            || inputs.len() > 16_384
            || !crate::content::hex_digest(authority_digest)
            || inputs.windows(2).any(|keys| keys[0] >= keys[1])
        {
            return Err("invalid bounded original content reader scope".into());
        }
        let mut originals = Vec::new();
        for content in inputs {
            content.validate()?;
            let id = format!("{reader_id}:{}", super::copy::digest(content)?);
            originals.push(ReaderIntent {
                schema: "roboguide.replica-content-reader/v0.1".into(),
                reader_id: id,
                receiver: self.identity().clone(),
                content: content.clone(),
                authority_digest: authority_digest.into(),
                budget: ContentBudget::issue(300_000)?,
                retention_only: true,
            });
        }
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        layout::validate(connection, self.identity())?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let mut readers = layout::head(&transaction, "reader_ids")?;
        let mut pins = layout::head(&transaction, "reference_ids")?;
        if readers.len().saturating_add(originals.len()) > 4096 {
            return Err("original reader scope reached its retained operation bound".into());
        }
        for original in &originals {
            let id = reader::key(&original.reader_id)?;
            if reader::record(&transaction, &id)?.is_some() {
                return Err("original content reader scope ID cannot reopen".into());
            }
            let reference = reader::reference(original)?;
            transaction
                .execute(
                    "INSERT INTO content_readers VALUES (?1,?2)",
                    params![
                        id,
                        serde_json::to_string(&ReaderRecord {
                            intent: original.clone(),
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
            readers.insert(id);
            pins.insert(reference.reference_id);
        }
        layout::write_head(&transaction, "reader_ids", &readers)?;
        layout::write_head(&transaction, "reference_ids", &pins)?;
        layout::within_budget(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(ReplicaContentReadLease {
            store: self.clone(),
            originals,
        })
    }
}

impl Drop for ReplicaContentReadLease {
    /// Ends only these actual original scope handles; failed durable closure keeps each original pin.
    fn drop(&mut self) {
        for original in &self.originals {
            let close = (|| -> Result<(), String> {
                let mut guard = self
                    .store
                    .inner
                    .connection
                    .lock()
                    .map_err(|error| error.to_string())?;
                let connection = guard
                    .as_mut()
                    .ok_or("original content connection is closed")?;
                layout::validate(connection, self.store.identity())?;
                let receipt = ReplicaContentReaderReceipt {
                    schema: "roboguide.replica-content-reader-receipt/v0.1".into(),
                    request_digest: super::copy::digest(original)?,
                    reader_id: original.reader_id.clone(),
                    content: original.content.clone(),
                    store_owner: self.store.identity().store_owner.clone(),
                    file_device: 0,
                    file_inode: 0,
                    bytes_read: 0,
                    observed_digest: artifact_store::digest_bytes(b""),
                    complete: false,
                    closed_ms: original.budget.closed_now()?,
                    retention_only: true,
                };
                reader::publish(
                    connection,
                    &ReaderRecord {
                        intent: original.clone(),
                        receipt: Some(receipt),
                        process_end: None,
                    },
                )
            })();
            if close.is_err() { /* Retained original Unknown is intentionally not discarded. */ }
        }
    }
}
