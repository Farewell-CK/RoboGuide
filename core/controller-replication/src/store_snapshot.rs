//! Actual Snapshot parent transactions; member-local producer authority never arrives from a sender.

use super::*;
use crate::content_store::{ProducerAdmission, ProducerPermit};

/// Actual original managed Snapshot work, separate from its independent accepted-view responsibility.
struct SnapshotProducerWork {
    /// Mandatory exact local heads captured before any parent effect.
    heads: super::producers::ProducerHeads,
    /// Actual single-use Snapshot scope, never copied from the sender.
    snapshot: Box<ProducerPermit>,
    /// Independent actual receiving view scope; empty initial snapshots have none.
    view: Option<Box<ProducerPermit>>,
}

impl SqliteRaftStore {
    /// Reads the exact actual last Snapshot with a new independent bounded managed reader scope.
    pub(super) fn current_snapshot_record(
        &self,
    ) -> Result<Option<Snapshot<ControllerRaftConfig>>, StorageError<u64>> {
        let connection = self.connection.lock().map_err(io_error)?;
        self.validate_content_producer_heads(&connection)
            .map_err(io_error)?;
        let saved: Option<(String, Vec<u8>)> = connection
            .query_row(
                "SELECT meta,data FROM replica_v1_snapshot WHERE singleton=1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .map_err(io_error)?;
        saved
            .map(|(meta, data)| {
                let body: StoreSnapshot = serde_json::from_slice(&data).map_err(io_error)?;
                self.validate_content_membership(body.membership.membership())
                    .map_err(io_error)?;
                let meta: SnapshotMeta<u64, ReplicaMember> =
                    serde_json::from_str(&meta).map_err(io_error)?;
                self.validate_content_membership(meta.last_membership.membership())
                    .map_err(io_error)?;
                Ok(Snapshot {
                    meta,
                    snapshot: Box::new(self.snapshot_data(&body, data)?),
                })
            })
            .transpose()
    }

    /// Publishes an explicitly identified original storage build; duplicate IDs never create another effect.
    pub fn build_content_snapshot_for(
        &self,
        operation_id: &str,
    ) -> Result<Snapshot<ControllerRaftConfig>, StorageError<u64>> {
        if !self.has_producer_journal() {
            return Err(io_error(
                "original Snapshot operation requires its explicit producer profile",
            ));
        }
        let (store, _capacity) = self.original_storage_work()?;
        store.build_snapshot_record(Some(operation_id))
    }

    /// Installs one explicitly identified original receiving operation; this grants no network quorum.
    pub fn install_content_snapshot_for(
        &self,
        operation_id: &str,
        meta: &SnapshotMeta<u64, ReplicaMember>,
        data: Vec<u8>,
    ) -> Result<(), StorageError<u64>> {
        if !self.has_producer_journal() {
            return Err(io_error(
                "original Snapshot operation requires its explicit producer profile",
            ));
        }
        let (store, _capacity) = self.original_storage_work()?;
        store.install_snapshot_record(meta, data, Some(operation_id))
    }

    /// Freezes an actual original receiving/build invocation before file hashes or parent publication.
    fn admit_snapshot_producer(
        &self,
        connection: &Connection,
        body: &StoreSnapshot,
        meta: &SnapshotMeta<u64, ReplicaMember>,
        data: &[u8],
        operation_id: Option<&str>,
        install: bool,
    ) -> Result<Option<SnapshotProducerWork>, StorageError<u64>> {
        if !self.has_producer_journal() {
            return Ok(None);
        }
        self.validate_content_producer_heads(connection)
            .map_err(io_error)?;
        let heads = super::producers::read_heads(connection)?;
        let generated;
        let operation = match operation_id {
            Some(operation) => operation,
            None => {
                generated = crate::supervision::new_process_instance().map_err(io_error)?;
                &generated
            }
        };
        if operation.is_empty() || operation.len() > 128 || operation.chars().any(char::is_control)
        {
            return Err(io_error(
                "original Snapshot invocation identity is unbounded",
            ));
        }
        let kind = if install {
            ReplicaContentProducerKind::SnapshotInstall
        } else {
            ReplicaContentProducerKind::SnapshotBuild
        };
        let local_id = format!(
            "producer:{:x}",
            Sha256::digest(serde_json::to_vec(&(kind, operation)).map_err(io_error)?)
        );
        let epoch = body
            .last_applied
            .map(|id| ControllerEpoch {
                term: id.leader_id.term,
                replica_id: id.leader_id.node_id,
            })
            .or_else(|| body.view.content.as_ref().map(|content| content.epoch))
            .ok_or_else(|| {
                io_error("original Snapshot epoch is unavailable; no epoch is fabricated")
            })?;
        let authority = format!("{:x}", Sha256::digest(data));
        let inputs = body
            .view
            .content
            .as_ref()
            .map(|content| content.inputs.as_slice())
            .unwrap_or_default();
        let source = ReplicaContentProducerSource {
            kind,
            epoch,
            position: meta.snapshot_id.clone(),
            authority_digest: authority,
            parent_record_digest: super::producers::snapshot_record_digest(meta, data)?,
            checkpoint_digest: body
                .view
                .checkpoint
                .as_ref()
                .map(state::checkpoint_identity),
        };
        let admission = self
            .begin_content_producer_for(&local_id, source, inputs)?
            .ok_or_else(|| io_error("original Snapshot producer admission is unavailable"))?;
        let snapshot = match admission {
            ProducerAdmission::New(permit) => permit,
            ProducerAdmission::Existing(original) => {
                if matches!(original.disposition.as_str(), "published" | "unknown")
                    && heads
                        .snapshot
                        .as_ref()
                        .is_some_and(|head| head.operation_id == original.operation_id)
                {
                    self.verify_view_content(&body.view)?;
                    return Ok(None);
                }
                return Err(io_error(
                    "original Snapshot producer is retained; unknown/retired operation cannot execute again",
                ));
            }
        };
        snapshot.check().map_err(io_error)?;
        let view = if install && let Some(state) = &body.view.content {
            let view_id = format!(
                "producer:{:x}",
                Sha256::digest(serde_json::to_vec(&(kind, operation, "view")).map_err(io_error)?)
            );
            let admission = self
                .begin_content_producer_for(
                    &view_id,
                    ReplicaContentProducerSource {
                        kind: ReplicaContentProducerKind::View,
                        epoch: state.epoch,
                        position: meta.snapshot_id.clone(),
                        authority_digest: state.batch_digest.clone(),
                        parent_record_digest: super::producers::view_record_digest(
                            &body.view.content,
                        )?,
                        checkpoint_digest: body
                            .view
                            .checkpoint
                            .as_ref()
                            .map(state::checkpoint_identity),
                    },
                    &state.inputs,
                )?
                .ok_or_else(|| io_error("original receiving view admission is missing"))?;
            match admission {
                ProducerAdmission::New(permit) => Some(permit),
                ProducerAdmission::Existing(_) => {
                    return Err(io_error(
                        "original receiving view remains retained; no repeated install is authorized",
                    ));
                }
            }
        } else {
            None
        };
        Ok(Some(SnapshotProducerWork {
            heads,
            snapshot,
            view,
        }))
    }

    /// Commits only this original local producer's head in the same actual parent transaction as bytes metadata.
    fn write_snapshot_producer_heads(
        &self,
        connection: &Connection,
        work: &mut SnapshotProducerWork,
    ) -> Result<(), StorageError<u64>> {
        work.snapshot.check().map_err(io_error)?;
        work.heads.snapshot = Some(super::producers::ProducerHead {
            operation_id: work.snapshot.operation_id().into(),
            record_digest: work.snapshot.source().parent_record_digest.clone(),
        });
        if let Some(view) = &work.view {
            view.check().map_err(io_error)?;
            work.heads.view = Some(super::producers::ProducerHead {
                operation_id: view.operation_id().into(),
                record_digest: view.source().parent_record_digest.clone(),
            });
        }
        write_meta(connection, super::producers::HEADS_KEY, &work.heads)
    }

    /// Observes exact actual committed Snapshot/view rows before original publication, then retires removed peers.
    fn complete_snapshot_producers(
        &self,
        connection: &Connection,
        work: SnapshotProducerWork,
    ) -> Result<(), StorageError<u64>> {
        self.validate_content_producer_heads(connection)
            .map_err(io_error)?;
        for permit in [Some(work.snapshot), work.view].into_iter().flatten() {
            let original = self
                .content
                .as_ref()
                .ok_or_else(|| io_error("original content backend is missing"))?
                .producer_status(permit.operation_id())
                .map_err(io_error)?
                .ok_or_else(|| io_error("original Snapshot journal disappeared"))?;
            let (present, proof) = self.producer_is_referenced(connection, &original)?;
            if !present {
                return Err(io_error(
                    "actual original Snapshot/view publication is absent",
                ));
            }
            self.publish_content_producer(Some(permit), &proof)?;
        }
        self.reconcile_content_producers_locked(connection)
            .map_err(io_error)?;
        Ok(())
    }

    /// Installs exact actual metadata with optional original managed producer operation.
    pub(super) fn install_snapshot_record(
        &self,
        meta: &SnapshotMeta<u64, ReplicaMember>,
        data: Vec<u8>,
        operation_id: Option<&str>,
    ) -> Result<(), StorageError<u64>> {
        if data.len() > MAX_SNAPSHOT_BYTES {
            return Err(io_error("replica snapshot exceeds bounds"));
        }
        let restored: StoreSnapshot = serde_json::from_slice(&data).map_err(io_error)?;
        self.validate_content_membership(restored.membership.membership())
            .map_err(io_error)?;
        self.validate_content_membership(meta.last_membership.membership())
            .map_err(io_error)?;
        if restored.profile != self.profile
            || restored.last_applied != meta.last_log_id
            || restored.membership != meta.last_membership
            || meta.snapshot_id
                != snapshot_identity(restored.last_applied, restored.view.revision, &data)
        {
            return Err(io_error("replica snapshot identity/pointers differ"));
        }
        restored
            .view
            .validate(&self.application_schema, &self.profile)?;
        if (self.content.is_none() && restored.view.content.is_some())
            || (self.content.is_some()
                && restored.view.checkpoint.is_some()
                && restored.view.content.is_none())
        {
            return Err(io_error(
                "snapshot cannot cross metadata-only/complete original recovery authority",
            ));
        }
        {
            let connection = self.connection.lock().map_err(io_error)?;
            self.require_snapshot_successor(&connection, &restored.view)?;
        }
        if self.controller_content
            && let Some(clock) = &self.work_clock
        {
            let inputs = restored
                .view
                .content
                .as_ref()
                .map(|content| content.inputs.iter().cloned().collect())
                .unwrap_or_default();
            self.wait_for_content_inputs(inputs, clock)?;
        }
        let mut producer_work = {
            let connection = self.connection.lock().map_err(io_error)?;
            self.admit_snapshot_producer(&connection, &restored, meta, &data, operation_id, true)?
        };
        if self.has_producer_journal() && producer_work.is_none() {
            return Ok(());
        }
        let content_authority = format!("{:x}", Sha256::digest(&data));
        let content_inputs = restored
            .view
            .content
            .as_ref()
            .map(|content| content.inputs.as_slice())
            .unwrap_or_default();
        let producer = format!("snapshot:{}", meta.snapshot_id);
        if !self.has_producer_journal() {
            self.pin_content(
                ReplicaContentReferenceKind::Snapshot,
                &producer,
                &content_authority,
                content_inputs,
            )?;
        }
        self.verify_view_content(&restored.view)?;
        if !self.has_producer_journal()
            && let Some(state) = &restored.view.content
        {
            self.pin_content(
                ReplicaContentReferenceKind::View,
                &format!("view:{}", state.batch_digest),
                &state.batch_digest,
                &state.inputs,
            )?;
        }
        if let Some(trust) = &restored.certificate_trust {
            trust.validate().map_err(io_error)?;
        }
        if let Some(record) = &restored.leader_transfer {
            let mut legacy = record.clone();
            legacy.state = "unknown".into();
            legacy.confirmed_epoch = None;
            validate_leader_transfer(&legacy).map_err(io_error)?;
        }
        let mut connection = self.connection.lock().map_err(io_error)?;
        let transaction = connection.transaction().map_err(io_error)?;
        let previous_snapshot: Option<Vec<u8>> = transaction
            .query_row(
                "SELECT data FROM replica_v1_snapshot WHERE singleton=1",
                [],
                |row| row.get(0),
            )
            .optional()
            .map_err(io_error)?;
        let previous_content: Option<AcceptedContentState> =
            read_meta::<Option<AcceptedContentState>>(&transaction, "content_state")?.flatten();
        self.require_snapshot_successor(&transaction, &restored.view)?;
        let original: Option<CertificateTrust> = read_meta(&transaction, "certificate_trust")?;
        match (&original, &restored.certificate_trust) {
            (None, None) => {}
            (Some(original), Some(incoming))
                if original.bootstrap == incoming.bootstrap
                    && incoming.revision >= original.revision
                    && incoming.receipts.starts_with(&original.receipts) =>
            {
                write_meta(&transaction, "certificate_trust", incoming)?;
            }
            _ => {
                return Err(io_error(
                    "snapshot certificate authority is absent, stale or forked",
                ));
            }
        }
        transaction
            .execute("DELETE FROM replica_v1_events", [])
            .map_err(io_error)?;
        transaction
            .execute("DELETE FROM replica_v1_commands", [])
            .map_err(io_error)?;
        for command in &restored.view.commands {
            transaction
                .execute(
                    "INSERT INTO replica_v1_commands VALUES (?1,?2,?3)",
                    params![
                        command.authority_revision,
                        command.command_id,
                        serde_json::to_string(command).map_err(io_error)?
                    ],
                )
                .map_err(io_error)?;
        }
        let first_sequence = restored
            .view
            .events
            .first()
            .map_or(0, |event| event.sequence);
        for (index, event) in restored.view.events.iter().enumerate() {
            let expected = if first_sequence == 0 {
                1
            } else {
                first_sequence + index as u64
            };
            if event.sequence != expected {
                return Err(io_error("replica snapshot evidence is discontinuous"));
            }
            transaction
                .execute(
                    "INSERT INTO replica_v1_events(sequence,event_id,data,digest) VALUES (?1,?2,?3,?4)",
                    params![
                        event.sequence,
                        event.event_id,
                        serde_json::to_string(event).map_err(io_error)?,
                        format!("{:x}",Sha256::digest(serde_json::to_vec(event).map_err(io_error)?))
                    ],
                )
                .map_err(io_error)?;
        }
        write_meta(&transaction, "membership", &restored.membership)?;
        write_meta(&transaction, "revision", &restored.view.revision)?;
        if self.content.is_some() {
            write_meta(&transaction, "content_state", &restored.view.content)?;
        }
        if let Some(checkpoint) = restored.view.checkpoint {
            write_meta(&transaction, "checkpoint", &checkpoint)?;
            write_meta(
                &transaction,
                "checkpoint_digest",
                &state::checkpoint_identity(&checkpoint),
            )?;
        } else {
            transaction
                .execute(
                    "DELETE FROM replica_v1_meta WHERE key IN ('checkpoint','checkpoint_digest')",
                    [],
                )
                .map_err(io_error)?;
        }
        if let Some(anchor) = restored.view.history_anchor {
            write_meta(&transaction, "history_anchor", &anchor)?;
        } else {
            transaction
                .execute("DELETE FROM replica_v1_meta WHERE key='history_anchor'", [])
                .map_err(io_error)?;
        }
        if let Some(applied) = restored.last_applied {
            write_meta(&transaction, "applied", &applied)?;
        } else {
            transaction
                .execute("DELETE FROM replica_v1_meta WHERE key='applied'", [])
                .map_err(io_error)?;
        }
        transaction.execute("INSERT INTO replica_v1_snapshot(singleton,meta,data) VALUES (1,?1,?2) ON CONFLICT(singleton) DO UPDATE SET meta=excluded.meta,data=excluded.data",
            params![serde_json::to_string(meta).map_err(io_error)?,data]).map_err(io_error)?;
        if let Some(work) = &mut producer_work {
            self.write_snapshot_producer_heads(&transaction, work)?;
        }
        transaction.commit().map_err(io_error)?;
        if let Some(work) = producer_work {
            self.complete_snapshot_producers(&connection, work)?;
            return Ok(());
        }
        if let Some(previous) = previous_snapshot {
            let original: StoreSnapshot = serde_json::from_slice(&previous).map_err(io_error)?;
            let authority = format!("{:x}", Sha256::digest(&previous));
            let original_id =
                snapshot_identity(original.last_applied, original.view.revision, &previous);
            if original_id != meta.snapshot_id {
                let inputs = original
                    .view
                    .content
                    .as_ref()
                    .map(|content| content.inputs.as_slice())
                    .unwrap_or_default();
                let pins = self.content_references(
                    ReplicaContentReferenceKind::Snapshot,
                    &format!("snapshot:{original_id}"),
                    &authority,
                    inputs,
                )?;
                self.release_content(&pins, &content_authority)?;
            }
        }
        if let Some(previous) = previous_content
            && read_view(&connection)?
                .content
                .as_ref()
                .is_none_or(|current| current.batch_digest != previous.batch_digest)
        {
            let pins = self.content_references(
                ReplicaContentReferenceKind::View,
                &format!("view:{}", previous.batch_digest),
                &previous.batch_digest,
                &previous.inputs,
            )?;
            self.release_content(&pins, &content_authority)?;
        }
        Ok(())
    }

    /// Publishes one exact actual Snapshot with its independent member-local producer operation.
    pub(super) fn build_snapshot_record(
        &self,
        operation_id: Option<&str>,
    ) -> Result<Snapshot<ControllerRaftConfig>, StorageError<u64>> {
        let mut connection = self.connection.lock().map_err(io_error)?;
        let snapshot = StoreSnapshot {
            profile: self.profile.clone(),
            last_applied: read_meta(&connection, "applied")?,
            membership: read_meta(&connection, "membership")?.unwrap_or_default(),
            view: read_view(&connection)?,
            certificate_trust: read_meta(&connection, "certificate_trust")?,
            leader_transfer: None,
        };
        let data = serde_json::to_vec(&snapshot).map_err(io_error)?;
        self.validate_content_membership(snapshot.membership.membership())
            .map_err(io_error)?;
        let meta = SnapshotMeta {
            last_log_id: snapshot.last_applied,
            last_membership: snapshot.membership.clone(),
            snapshot_id: snapshot_identity(snapshot.last_applied, snapshot.view.revision, &data),
        };
        if data.len() > MAX_SNAPSHOT_BYTES {
            return Err(io_error("replica snapshot exceeds bounds"));
        }
        snapshot
            .view
            .validate(&self.application_schema, &self.profile)?;
        let mut producer_work = self.admit_snapshot_producer(
            &connection,
            &snapshot,
            &meta,
            &data,
            operation_id,
            false,
        )?;
        if self.has_producer_journal() && producer_work.is_none() {
            return Ok(Snapshot {
                meta,
                snapshot: Box::new(self.snapshot_data(&snapshot, data)?),
            });
        }
        let authority = format!("{:x}", Sha256::digest(&data));
        let inputs = snapshot
            .view
            .content
            .as_ref()
            .map(|state| state.inputs.as_slice())
            .unwrap_or_default();
        if !self.has_producer_journal() {
            self.pin_content(
                ReplicaContentReferenceKind::Snapshot,
                &format!("snapshot:{}", meta.snapshot_id),
                &authority,
                inputs,
            )?;
        }
        self.verify_view_content(&snapshot.view)?;
        let previous: Option<Vec<u8>> = connection
            .query_row(
                "SELECT data FROM replica_v1_snapshot WHERE singleton=1",
                [],
                |row| row.get(0),
            )
            .optional()
            .map_err(io_error)?;
        let transaction = connection.transaction().map_err(io_error)?;
        transaction.execute("INSERT INTO replica_v1_snapshot(singleton,meta,data) VALUES (1,?1,?2) ON CONFLICT(singleton) DO UPDATE SET meta=excluded.meta,data=excluded.data",
            params![serde_json::to_string(&meta).map_err(io_error)?,data]).map_err(io_error)?;
        if let Some(work) = &mut producer_work {
            self.write_snapshot_producer_heads(&transaction, work)?;
        }
        transaction.commit().map_err(io_error)?;
        if let Some(work) = producer_work {
            self.complete_snapshot_producers(&connection, work)?;
            return Ok(Snapshot {
                meta,
                snapshot: Box::new(self.snapshot_data(&snapshot, data)?),
            });
        }
        if let Some(previous) = previous {
            let original: StoreSnapshot = serde_json::from_slice(&previous).map_err(io_error)?;
            let original_id =
                snapshot_identity(original.last_applied, original.view.revision, &previous);
            if original_id != meta.snapshot_id {
                let original_authority = format!("{:x}", Sha256::digest(&previous));
                let inputs = original
                    .view
                    .content
                    .as_ref()
                    .map(|state| state.inputs.as_slice())
                    .unwrap_or_default();
                let pins = self.content_references(
                    ReplicaContentReferenceKind::Snapshot,
                    &format!("snapshot:{original_id}"),
                    &original_authority,
                    inputs,
                )?;
                self.release_content(&pins, &authority)?;
            }
        }
        Ok(Snapshot {
            meta,
            snapshot: Box::new(self.snapshot_data(&snapshot, data)?),
        })
    }
}
