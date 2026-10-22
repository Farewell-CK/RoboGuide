//! Complete-content Raft storage helpers; accepted metadata never implies an application ACK or bytes.

use super::*;

impl SqliteRaftStore {
    /// Checks original incoming Snapshot monotonicity before content pin effects and again under Commit.
    pub(super) fn require_snapshot_successor(
        &self,
        connection: &Connection,
        incoming: &ReplicaView,
    ) -> Result<(), StorageError<u64>> {
        if self.content.is_none() {
            return Ok(());
        }
        let previous = read_view(connection)?;
        if incoming.revision < previous.revision
            || !incoming.commands.starts_with(&previous.commands)
            || (incoming.revision == previous.revision
                && (incoming.checkpoint != previous.checkpoint
                    || incoming.content != previous.content
                    || incoming.events != previous.events))
        {
            return Err(io_error(
                "complete original Snapshot cannot regress or fork accepted authority",
            ));
        }
        if let Some(previous) = &previous.content {
            let current = incoming
                .content
                .as_ref()
                .ok_or_else(|| io_error("Snapshot lost original complete content authority"))?;
            current
                .recovery
                .follows(&previous.recovery)
                .map_err(io_error)?;
            if previous
                .origins
                .iter()
                .any(|(id, origin)| current.origins.get(id) != Some(origin))
            {
                return Err(io_error(
                    "Snapshot changed original accepted content lineage",
                ));
            }
        }
        Ok(())
    }

    /// Pins exact incoming original log inputs before their Raft SQL append can expose the candidate.
    pub(super) fn retain_log_inputs(
        &self,
        entry: &Entry<ControllerRaftConfig>,
    ) -> Result<Option<crate::content_store::ProducerAdmission>, StorageError<u64>> {
        if let EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application: candidate,
        }) = &entry.payload
        {
            if self.content.is_none() {
                return Err(io_error(
                    "metadata-only storage refuses complete-content log admission",
                ));
            }
            let digest = candidate.identity().map_err(io_error)?;
            if self.has_producer_journal() {
                let raw = serde_json::to_string(entry).map_err(io_error)?;
                return self.begin_content_producer(
                    ReplicaContentProducerSource {
                        kind: ReplicaContentProducerKind::Log,
                        epoch: ControllerEpoch {
                            term: entry.log_id.leader_id.term,
                            replica_id: entry.log_id.leader_id.node_id,
                        },
                        position: serde_json::to_string(&entry.log_id).map_err(io_error)?,
                        authority_digest: digest,
                        parent_record_digest: format!("{:x}", Sha256::digest(raw.as_bytes())),
                        checkpoint_digest: Some(state::checkpoint_identity(
                            &candidate.batch.checkpoint,
                        )),
                    },
                    &candidate.inputs,
                );
            }
            let producer = format!(
                "log:{}:{}:{}",
                entry.log_id.leader_id.term, entry.log_id.leader_id.node_id, entry.log_id.index
            );
            self.pin_content(
                ReplicaContentReferenceKind::Log,
                &producer,
                &digest,
                &candidate.inputs,
            )?;
        }
        Ok(None)
    }

    /// Derives exact old log pins while the original SQL still retains those immutable candidate rows.
    pub(super) fn removed_log_pins(
        &self,
        connection: &Connection,
        index: u64,
        suffix: bool,
    ) -> Result<Vec<ReplicaContentReference>, StorageError<u64>> {
        if self.content.is_none() || self.has_producer_journal() {
            return Ok(Vec::new());
        }
        let operator = if suffix { ">=" } else { "<=" };
        let mut statement = connection
            .prepare(&format!(
                "SELECT data FROM replica_v1_log WHERE log_index{operator}?1 ORDER BY log_index"
            ))
            .map_err(io_error)?;
        let mut pins = Vec::new();
        for row in statement
            .query_map([index], |row| row.get::<_, String>(0))
            .map_err(io_error)?
        {
            let entry: Entry<ControllerRaftConfig> =
                serde_json::from_str(&row.map_err(io_error)?).map_err(io_error)?;
            if let EntryPayload::Normal(ReplicaCommand::ContentApplication {
                content_application: candidate,
            }) = &entry.payload
            {
                let digest = candidate.identity().map_err(io_error)?;
                let producer = format!(
                    "log:{}:{}:{}",
                    entry.log_id.leader_id.term, entry.log_id.leader_id.node_id, entry.log_id.index
                );
                pins.extend(self.content_references(
                    ReplicaContentReferenceKind::Log,
                    &producer,
                    &digest,
                    &candidate.inputs,
                )?);
            }
        }
        Ok(pins)
    }

    /// Persists an exact independent original producer set before its payload/log/Snapshot effects.
    pub(super) fn pin_content(
        &self,
        kind: ReplicaContentReferenceKind,
        producer: &str,
        authority: &str,
        inputs: &[ArtifactContentKey],
    ) -> Result<Vec<ReplicaContentReference>, StorageError<u64>> {
        let Some(content) = &self.content else {
            if inputs.is_empty() {
                return Ok(Vec::new());
            }
            return Err(io_error(
                "metadata-only store refuses original content producer",
            ));
        };
        let mut pins = Vec::new();
        for key in inputs {
            let digest = format!(
                "{:x}",
                Sha256::digest(
                    serde_json::to_vec(&serde_json::to_value(key).map_err(io_error)?)
                        .map_err(io_error)?
                )
            );
            let pin = ReplicaContentReference {
                reference_id: format!("{producer}:{digest}"),
                kind,
                content: key.clone(),
                authority_digest: authority.into(),
                generation: 1,
            };
            if !content.acquire_reference(&pin).map_err(io_error)? {
                return Err(io_error(
                    "original producer pin was retired; resurrection refused",
                ));
            }
            pins.push(pin);
        }
        Ok(pins)
    }

    /// Retires one known replaced producer only after original actual SQL proves its removal.
    pub(super) fn release_content(
        &self,
        pins: &[ReplicaContentReference],
        proof: &str,
    ) -> Result<(), StorageError<u64>> {
        let Some(content) = &self.content else {
            return Ok(());
        };
        for pin in pins {
            content.release_reference(pin, proof).map_err(io_error)?;
        }
        Ok(())
    }

    /// Derives only one exact retained producer set from immutable original source rows.
    pub(super) fn content_references(
        &self,
        kind: ReplicaContentReferenceKind,
        producer: &str,
        authority: &str,
        inputs: &[ArtifactContentKey],
    ) -> Result<Vec<ReplicaContentReference>, StorageError<u64>> {
        inputs
            .iter()
            .map(|key| {
                Ok(ReplicaContentReference {
                    reference_id: format!(
                        "{producer}:{:x}",
                        Sha256::digest(
                            serde_json::to_vec(&serde_json::to_value(key).map_err(io_error)?)
                                .map_err(io_error)?
                        )
                    ),
                    kind,
                    content: key.clone(),
                    authority_digest: authority.into(),
                    generation: 1,
                })
            })
            .collect()
    }

    /// Opens an independent actual upstream Snapshot reader, never borrowing the Snapshot's own pin.
    pub(super) fn snapshot_data(
        &self,
        body: &StoreSnapshot,
        data: Vec<u8>,
    ) -> Result<ReplicaSnapshotData, StorageError<u64>> {
        let Some(store) = &self.content else {
            return Ok(ReplicaSnapshotData::new(data));
        };
        let inputs = body
            .view
            .content
            .as_ref()
            .map(|content| content.inputs.as_slice())
            .unwrap_or_default();
        let authority = format!("{:x}", Sha256::digest(&data));
        let operation = crate::supervision::new_process_instance().map_err(io_error)?;
        let lease = store
            .retain_reader_scope(&operation, inputs, &authority)
            .map_err(io_error)?;
        for key in inputs {
            store.verify_content(key).map_err(io_error)?;
        }
        Ok(ReplicaSnapshotData::retained(data, lease))
    }

    /// Verifies every required original byte under the exact retained local backend before publish/install.
    pub(super) fn verify_view_content(&self, view: &ReplicaView) -> Result<(), StorageError<u64>> {
        let Some(store) = &self.content else {
            return Ok(());
        };
        if let Some(state) = &view.content {
            for key in &state.inputs {
                store.verify_content(key).map_err(io_error)?;
            }
        } else if view.checkpoint.is_some() {
            return Err(io_error("complete original content view is unavailable"));
        }
        Ok(())
    }

    /// Accepts exact complete auxiliary state only in the explicit owner-bound storage generation.
    /// The outer original Raft SQL transaction still commits all metadata atomically.
    pub(super) fn apply_content_batch(
        &self,
        connection: &Connection,
        candidate: &ContentApplicationBatch,
        admission: Option<&crate::content_store::ProducerAdmission>,
    ) -> Result<CommitReceipt, StorageError<u64>> {
        let previous = read_view(connection)?;
        let rejected = |message: String| CommitReceipt {
            accepted: false,
            revision: previous.revision,
            checkpoint_digest: previous.checkpoint.as_ref().map(state::checkpoint_identity),
            rejection: Some(message),
            certificate: None,
        };
        let Some(content) = &self.content else {
            return Ok(rejected(
                "metadata-only Replica storage refuses complete-content authority".into(),
            ));
        };
        if self.has_producer_journal() {
            match admission {
                Some(crate::content_store::ProducerAdmission::New(permit)) => {
                    permit.check().map_err(io_error)?
                }
                Some(crate::content_store::ProducerAdmission::Existing(original)) => {
                    let heads = super::producers::read_heads(connection)?;
                    let accepted = candidate.accepted_state().map_err(io_error)?;
                    if matches!(original.disposition.as_str(), "unknown" | "published")
                        && heads
                            .view
                            .as_ref()
                            .is_some_and(|head| head.operation_id == original.operation_id)
                        && previous.content.as_ref() == Some(&accepted)
                    {
                        return Ok(CommitReceipt {
                            accepted: true,
                            revision: previous.revision,
                            checkpoint_digest: previous
                                .checkpoint
                                .as_ref()
                                .map(state::checkpoint_identity),
                            rejection: None,
                            certificate: None,
                        });
                    }
                    return Err(io_error(
                        "original view producer is retained; unknown/retired apply cannot execute again",
                    ));
                }
                None => {
                    return Err(io_error(
                        "original view admission is mandatory before apply",
                    ));
                }
            }
        }
        if let Err(error) = candidate.validate_against(&previous, &self.profile) {
            return Ok(rejected(error));
        }
        let mut producer_heads = if self.has_producer_journal() {
            Some(super::producers::read_heads(connection)?)
        } else {
            None
        };
        let digest = candidate.identity().map_err(io_error)?;
        for key in &candidate.inputs {
            if self.has_producer_journal() {
                content.verify_content(key).map_err(io_error)?;
                continue;
            }
            let encoded = serde_json::to_vec(&serde_json::to_value(key).map_err(io_error)?)
                .map_err(io_error)?;
            let reference = ReplicaContentReference {
                reference_id: format!("view:{digest}:{:x}", Sha256::digest(encoded)),
                kind: ReplicaContentReferenceKind::View,
                content: key.clone(),
                authority_digest: digest.clone(),
                generation: 1,
            };
            if !content.acquire_reference(&reference).map_err(io_error)? {
                return Err(io_error(
                    "original complete view pin was already retired; new authority refused",
                ));
            }
        }
        let receipt = self.apply_batch(connection, &candidate.batch)?;
        if receipt.accepted {
            write_meta(
                connection,
                "content_state",
                &Some(candidate.accepted_state().map_err(io_error)?),
            )?;
            if let Some(crate::content_store::ProducerAdmission::New(permit)) = admission {
                permit.check().map_err(io_error)?;
                let heads = producer_heads
                    .as_mut()
                    .ok_or_else(|| io_error("original view producer heads are missing"))?;
                heads.view = Some(super::producers::ProducerHead {
                    operation_id: permit.operation_id().into(),
                    record_digest: permit.source().parent_record_digest.clone(),
                });
                write_meta(connection, super::producers::HEADS_KEY, heads)?;
            }
        }
        Ok(receipt)
    }

    /// Reads actual local qualified bytes independently of accepted metadata and historical copy receipts.
    /// Callers still need actual current epoch/membership and complete same-member observations.
    pub fn verify_accepted_content(&self) -> Result<(), String> {
        let content = self
            .content
            .as_ref()
            .ok_or("metadata-only Replica has no content authority")?;
        let view = self.accepted_view()?;
        view.validate(&self.application_schema, &self.profile)
            .map_err(|error| error.to_string())?;
        if let Some(state) = &view.content {
            for key in &state.inputs {
                content.verify_content(key)?;
            }
        } else if view.checkpoint.is_some() {
            return Err("complete original accepted content is unavailable".into());
        }
        Ok(())
    }
}
