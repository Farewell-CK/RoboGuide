//! Unique original receive worker, retaining actual FD/root/source permission through final scope closure.

use super::*;
use sha2::{Digest as _, Sha256};
use std::io::Write;
use std::os::unix::fs::DirBuilderExt;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::PathBuf;

/// Live adapter permission, deliberately not serializable or constructible from a wire `ok` field.
pub(crate) type StreamPermission = Arc<dyn Fn() -> Result<(), String> + Send + Sync>;

/// Duplicate original operations return evidence only; no second FD/worker/budget is granted.
pub(crate) enum StreamAdmission {
    /// Exact newly admitted worker, which must stay inside its actual owned blocking scope.
    New(Box<StreamWriter>),
    /// Retained original outcome, including Unknown, never permission to repeat payload I/O.
    Existing(Box<ReplicaContentStreamStatus>),
}

/// One actual non-Clone worker owns all original write capability and its bounded capacity.
pub(crate) struct StreamWriter {
    /// Original managed root lifetime, independent of cancellation of a waiting caller.
    store: ReplicaContentStore,
    /// Complete immutable first source/target/process/budget evidence.
    intent: StreamIntent,
    /// Exact immutable first admission digest, checked against actual original SQL before I/O.
    request_digest: String,
    /// Actual physical backing frozen for this new worker; logical receiver keeps original provenance.
    backing: (u64, u64, u64, u64),
    /// Live source authorization retained by the actual worker, never by a JSON receipt alone.
    permission: StreamPermission,
    /// Actual owned original staging descriptor, closed before actual scope-end publication.
    file: Option<File>,
    /// Original path-safe staging leaf; old partial files are never reused or truncated.
    staging: PathBuf,
    /// Actual original stream order; another connection cannot rewind a live worker.
    received: u64,
    /// Bounded incremental actual payload digest, independent of caller-declared completion.
    hash: Sha256,
    /// Any failed original I/O/order/permission check irreversibly fences this live writer.
    failed: bool,
    /// Actual running/queued capacity held through every FD and receipt operation.
    _capacity: tokio::sync::OwnedSemaphorePermit,
}

impl ReplicaContentStore {
    /// Admits one real worker after complete durable intent/pin; trusted adapters supply live permission.
    pub(crate) fn admit_stream(
        &self,
        command: &str,
        target: ContentQuorumTarget,
        content: ArtifactContentKey,
        origin: StreamOrigin,
        budget: ContentBudget,
        permission: StreamPermission,
    ) -> Result<StreamAdmission, String> {
        if !self.inner.streams {
            return Err("original stream admission requires its explicit v4 root".into());
        }
        let intent = StreamIntent {
            schema: "roboguide.replica-content-stream/v0.1".into(),
            command_id: command.into(),
            receiver: self.identity().clone(),
            target,
            content,
            origin,
            budget,
        };
        validate_intent(&intent, self.identity())?;
        let id = operation_id(command)?;
        let request_digest = digest(&intent)?;
        let capacity;
        {
            let mut guard = self
                .inner
                .connection
                .lock()
                .map_err(|error| error.to_string())?;
            let connection = guard.as_mut().ok_or("original stream SQL is closed")?;
            self.validate_connection(connection)?;
            if let Some(original) = record(connection, &id)? {
                if original.intent.receiver != intent.receiver
                    || original.intent.target != intent.target
                    || original.intent.content != intent.content
                    || original.intent.origin != intent.origin
                    || original.intent.budget.duration_ms != intent.budget.duration_ms
                {
                    return Err(
                        "original stream command changed its immutable source/target/budget".into(),
                    );
                }
                return Ok(StreamAdmission::Existing(Box::new(status(&original)?)));
            }
            capacity = self
                .inner
                .stream_capacity
                .clone()
                .try_acquire_owned()
                .map_err(|_| "original stream worker capacity is exhausted")?;
            (permission)()?;
            intent.budget.check()?;
            let transaction = connection
                .transaction()
                .map_err(|error| error.to_string())?;
            let mut heads = layout::head(&transaction, "stream_ids")?;
            let mut pins = layout::head(&transaction, "reference_ids")?;
            if heads.len() >= MAX_STREAMS || pins.len() >= MAX_REFERENCES {
                return Err("original stream/reference history reached its fixed bound".into());
            }
            let original = StreamRecord {
                intent: intent.clone(),
                request_digest: request_digest.clone(),
                receipt: None,
                worker_closed_ms: None,
            };
            let raw = serde_json::to_string(&original).map_err(|error| error.to_string())?;
            if raw.len() > 8 * 1024 * 1024 {
                return Err("original stream record exceeds its fixed bound".into());
            }
            transaction.execute("INSERT INTO content_streams(stream_id,request_digest,record) VALUES (?1,?2,?3)", params![id, request_digest,raw]).map_err(|error| error.to_string())?;
            let reference = reference(&intent)?;
            transaction
                .execute(
                    "INSERT INTO content_references(reference_id,record) VALUES (?1,?2)",
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
            pins.insert(reference.reference_id);
            heads.insert(id.clone());
            layout::write_head(&transaction, "reference_ids", &pins)?;
            layout::write_head(&transaction, "stream_ids", &heads)?;
            layout::within_budget(&transaction)?;
            intent.budget.check()?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        let mut worker = Box::new(StreamWriter {
            backing: super::super::restore::stream_backing(&self.inner)?,
            store: self.clone(),
            intent,
            request_digest,
            permission,
            file: None,
            staging: self.root().join("staging-v1").join(format!("stream-{id}")),
            received: 0,
            hash: Sha256::new(),
            failed: false,
            _capacity: capacity,
        });
        worker.check()?;
        worker.file = Some(layout::new_file(&worker.staging)?);
        worker.check()?;
        Ok(StreamAdmission::New(worker))
    }

    /// Observes only a complete original stream; absent records do not authorize reusing lost IDs.
    pub fn stream_status(
        &self,
        command: &str,
    ) -> Result<Option<ReplicaContentStreamStatus>, String> {
        if !self.inner.streams {
            return Err("original stream observation requires its explicit v4 root".into());
        }
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard.as_ref().ok_or("original stream SQL is closed")?;
        self.validate_connection(connection)?;
        record(connection, &operation_id(command)?)?
            .as_ref()
            .map(status)
            .transpose()
    }
}

impl StreamWriter {
    /// Returns only this already durable original admission identity for the subsequent Ready notice.
    pub(crate) fn original_digest(&self) -> &str {
        &self.request_digest
    }
    /// Checks the exact original worker before its adapter opens/reads another source payload chunk.
    pub(crate) fn check_before_source_read(&mut self) -> Result<(), String> {
        let result = self.check();
        if result.is_err() {
            self.failed = true;
        }
        result
    }

    /// Checks actual original authority, clock, root backing and exact admitted row without renewing them.
    fn check(&self) -> Result<(), String> {
        if self.failed {
            return Err("original stream writer is fenced after its earlier failure".into());
        }
        self.intent.budget.check()?;
        (self.permission)()?;
        layout::directory(self.store.root())?;
        let root =
            std::fs::symlink_metadata(self.store.root()).map_err(|error| error.to_string())?;
        let database = std::fs::symlink_metadata(self.store.root().join("content.sqlite3"))
            .map_err(|error| error.to_string())?;
        if (root.dev(), root.ino()) != (self.backing.0, self.backing.1)
            || !database.is_file()
            || (database.dev(), database.ino()) != (self.backing.2, self.backing.3)
        {
            return Err("original stream root/SQL backing identity changed".into());
        }
        let guard = self
            .store
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard.as_ref().ok_or("original stream SQL is closed")?;
        let actual: Option<String> = connection
            .query_row(
                "SELECT request_digest FROM content_streams WHERE stream_id=?1",
                [operation_id(&self.intent.command_id)?],
                |row| row.get(0),
            )
            .optional()
            .map_err(|error| error.to_string())?;
        if actual.as_ref() != Some(&self.request_digest) {
            return Err("original stream admitted row disappeared or changed".into());
        }
        Ok(())
    }

    /// Writes only one bounded exact next chunk under the actual original permission and budget.
    pub(crate) fn write_chunk(&mut self, offset: u64, bytes: &[u8]) -> Result<(), String> {
        let result = (|| -> Result<(), String> {
            self.check()?;
            let next = self
                .received
                .checked_add(bytes.len() as u64)
                .ok_or("original stream size overflow")?;
            if bytes.is_empty()
                || bytes.len() > CHUNK_BYTES
                || offset != self.received
                || next > self.intent.content.byte_size
            {
                return Err("original stream chunk/order/size differs".into());
            }
            self.file
                .as_mut()
                .ok_or("original stream FD is closed")?
                .write_all(bytes)
                .map_err(|error| error.to_string())?;
            self.hash.update(bytes);
            self.received = next;
            self.check()
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }

    /// Publishes only exact full bytes and original durable receipt; any error retains Unknown and pins.
    pub(crate) fn finish(mut self) -> Result<ReplicaContentStreamStatus, String> {
        self.check()?;
        if self.received != self.intent.content.byte_size
            || format!("sha256:{:x}", self.hash.clone().finalize())
                != self.intent.content.content_digest
        {
            return Err("original stream complete size/hash differs".into());
        }
        {
            let guard = self
                .store
                .inner
                .connection
                .lock()
                .map_err(|error| error.to_string())?;
            self.store
                .validate_connection(guard.as_ref().ok_or("original stream SQL is closed")?)?;
        }
        let file = self.file.take().ok_or("original stream FD is closed")?;
        file.set_permissions(std::fs::Permissions::from_mode(0o400))
            .map_err(|error| error.to_string())?;
        file.sync_all().map_err(|error| error.to_string())?;
        self.check()?;
        let destination = super::super::copy::object_path(self.store.root(), &self.intent.content);
        let parent = destination
            .parent()
            .ok_or("original stream destination parent is absent")?;
        if !parent.exists() {
            match std::fs::DirBuilder::new().mode(0o700).create(parent) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error.to_string()),
            }
        }
        layout::directory(parent)?;
        match std::fs::hard_link(&self.staging, &destination) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error.to_string()),
        }
        self.check()?;
        let actual =
            super::super::copy::verify_file_with_check(&destination, &self.intent.content, || {
                self.check()
            })?;
        let metadata = actual.metadata().map_err(|error| error.to_string())?;
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        File::open(self.store.root().join("content-v1"))
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        self.check()?;
        drop(actual);
        drop(file);
        std::fs::remove_file(&self.staging).map_err(|error| error.to_string())?;
        File::open(self.store.root().join("staging-v1"))
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        self.check()?;
        let receipt = ReplicaContentReceipt {
            schema: RECEIPT_SCHEMA.into(),
            request_digest: self.request_digest.clone(),
            command_id: self.intent.command_id.clone(),
            content: self.intent.content.clone(),
            store_owner: self.intent.receiver.store_owner.clone(),
            member: self.intent.receiver.member,
            profile_identity: self.intent.receiver.profile_identity.clone(),
            file_device: metadata.dev(),
            file_inode: metadata.ino(),
            completed_ms: self.intent.budget.check()?,
        };
        let mut guard = self
            .store
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard.as_mut().ok_or("original stream SQL is closed")?;
        self.store.validate_connection(connection)?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let id = operation_id(&self.intent.command_id)?;
        let mut original =
            record(&transaction, &id)?.ok_or("original stream admission disappeared")?;
        if original.intent != self.intent
            || original.receipt.is_some()
            || original.worker_closed_ms.is_some()
        {
            return Err("original stream changed before byte receipt publication".into());
        }
        original.receipt = Some(receipt.clone());
        original.worker_closed_ms = Some(self.intent.budget.closed_now()?);
        transaction
            .execute(
                "UPDATE content_streams SET record=?2 WHERE stream_id=?1",
                params![
                    id,
                    serde_json::to_string(&original).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        let key = digest(&self.intent.content)?;
        transaction.execute("INSERT INTO content_objects(content_key,record) VALUES (?1,?2) ON CONFLICT(content_key) DO NOTHING",params![key,serde_json::to_string(&receipt).map_err(|error| error.to_string())?]).map_err(|error| error.to_string())?;
        let mut objects = layout::head(&transaction, "content_keys")?;
        objects.insert(key);
        layout::write_head(&transaction, "content_keys", &objects)?;
        layout::within_budget(&transaction)?;
        self.intent.budget.check()?;
        (self.permission)()?;
        transaction.commit().map_err(|error| error.to_string())?;
        status(&original)
    }
}

impl Drop for StreamWriter {
    /// Closes the actual original FD first; failed SQL closure never invents completion or releases pins.
    fn drop(&mut self) {
        drop(self.file.take());
        let close = (|| -> Result<(), String> {
            let closed = self.intent.budget.closed_now()?;
            let mut guard = self
                .store
                .inner
                .connection
                .lock()
                .map_err(|error| error.to_string())?;
            let connection = guard.as_mut().ok_or("original stream SQL is closed")?;
            self.store.validate_connection(connection)?;
            let transaction = connection
                .transaction()
                .map_err(|error| error.to_string())?;
            let id = operation_id(&self.intent.command_id)?;
            let mut original =
                record(&transaction, &id)?.ok_or("original stream admission disappeared")?;
            if original.intent != self.intent {
                return Err("original stream changed before actual scope closure".into());
            }
            if original.worker_closed_ms.is_none() {
                original.worker_closed_ms = Some(closed);
                transaction
                    .execute(
                        "UPDATE content_streams SET record=?2 WHERE stream_id=?1",
                        params![
                            id,
                            serde_json::to_string(&original).map_err(|error| error.to_string())?
                        ],
                    )
                    .map_err(|error| error.to_string())?;
                layout::within_budget(&transaction)?;
            }
            transaction.commit().map_err(|error| error.to_string())
        })();
        let _ = close;
    }
}
