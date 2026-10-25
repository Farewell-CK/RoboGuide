//! Exact actual application transaction; original view permits span commit/rollback and publication.

use super::*;

impl SqliteRaftStore {
    /// Waits only for real publication within the already-issued original worker clock, without locks.
    pub(super) fn wait_for_apply_inputs(
        &self,
        entries: &[Entry<ControllerRaftConfig>],
        clock: &crate::content_store::ProducerClock,
    ) -> Result<(), StorageError<u64>> {
        let mut pending = BTreeSet::new();
        for entry in entries {
            if let EntryPayload::Normal(ReplicaCommand::ContentApplication {
                content_application,
            }) = &entry.payload
            {
                for key in &content_application.inputs {
                    pending.insert(key.clone());
                    if pending.len() > 65_536 {
                        return Err(io_error(
                            "original apply inputs exceed the retained Root inventory bound",
                        ));
                    }
                }
            }
        }
        self.wait_for_content_inputs(pending, clock)
    }

    /// Shares the original no-parent-lock readiness bound between apply and validated Snapshot inputs.
    pub(super) fn wait_for_content_inputs(
        &self,
        mut pending: BTreeSet<ArtifactContentKey>,
        clock: &crate::content_store::ProducerClock,
    ) -> Result<(), StorageError<u64>> {
        if pending.len() > 65_536 {
            return Err(io_error(
                "original content inputs exceed the retained Root inventory bound",
            ));
        }
        if pending.is_empty() {
            return Ok(());
        }
        let root = self
            .content
            .as_ref()
            .ok_or_else(|| io_error("original content backend is absent"))?;
        while !pending.is_empty() {
            clock.check().map_err(io_error)?;
            let mut available = Vec::new();
            for key in &pending {
                clock.check().map_err(io_error)?;
                if root.retained_content_is_available(key).map_err(io_error)? {
                    available.push(key.clone());
                }
            }
            clock.check().map_err(io_error)?;
            for key in available {
                pending.remove(&key);
            }
            if !pending.is_empty() {
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        }
        Ok(())
    }

    /// Applies actual original entries inside one parent transaction; caller retains managed queue ownership.
    pub(super) fn apply_entries(
        &self,
        entries: Vec<Entry<ControllerRaftConfig>>,
    ) -> Result<Vec<CommitReceipt>, StorageError<u64>> {
        if self.network_content
            && entries.iter().any(|entry| {
                matches!(
                    entry.payload,
                    EntryPayload::Normal(ReplicaCommand::ContentApplication { .. })
                )
            })
        {
            let mut replies = Vec::new();
            let mut prefix = Vec::new();
            for entry in entries {
                if matches!(
                    entry.payload,
                    EntryPayload::Normal(ReplicaCommand::ContentApplication { .. })
                ) {
                    if !prefix.is_empty() {
                        replies.extend(self.apply_entry_transaction(std::mem::take(&mut prefix))?);
                    }
                    replies.extend(self.apply_entry_transaction(vec![entry])?);
                } else {
                    prefix.push(entry);
                }
            }
            if !prefix.is_empty() {
                replies.extend(self.apply_entry_transaction(prefix)?);
            }
            return Ok(replies);
        }
        self.apply_entry_transaction(entries)
    }

    /// Publishes consensus-only prefixes before a content wait; every original command stays atomic.
    fn apply_entry_transaction(
        &self,
        entries: Vec<Entry<ControllerRaftConfig>>,
    ) -> Result<Vec<CommitReceipt>, StorageError<u64>> {
        if self.network_content
            && let Some(clock) = &self.work_clock
        {
            self.wait_for_apply_inputs(&entries, clock)?;
        }
        for entry in &entries {
            if let EntryPayload::Membership(membership) = &entry.payload {
                self.validate_content_membership(membership)
                    .map_err(io_error)?;
            }
        }
        let admissions = entries
            .iter()
            .map(|entry| self.retain_view_inputs(entry))
            .collect::<Result<Vec<_>, _>>()?;
        let mut view_permits = Vec::new();
        let mut connection = self.connection.lock().map_err(io_error)?;
        self.validate_content_producer_heads(&connection)
            .map_err(io_error)?;
        let transaction = connection.transaction().map_err(io_error)?;
        let mut replies = Vec::new();
        let mut replaced_views = Vec::<AcceptedContentState>::new();
        for (entry, admission) in entries.into_iter().zip(admissions) {
            let retained_view = matches!(
                &admission,
                Some(crate::content_store::ProducerAdmission::Existing(_))
            );
            let receipt = match &entry.payload {
                EntryPayload::Normal(ReplicaCommand::ApplicationUpgradeAbort {
                    application_upgrade_abort,
                }) => self.apply_application_upgrade_abort(
                    &transaction,
                    application_upgrade_abort.as_ref().clone(),
                )?,
                EntryPayload::Normal(ReplicaCommand::ApplicationUpgrade {
                    application_upgrade,
                }) => self.apply_application_upgrade(
                    &transaction,
                    application_upgrade.as_ref().clone(),
                )?,
                EntryPayload::Blank => CommitReceipt::default(),
                EntryPayload::Membership(membership) => {
                    write_meta(
                        &transaction,
                        "membership",
                        &StoredMembership::new(Some(entry.log_id), membership.clone()),
                    )?;
                    CommitReceipt::default()
                }
                EntryPayload::Normal(ReplicaCommand::Application(batch)) => {
                    if self.content.is_some() {
                        CommitReceipt {
                            rejection: Some(
                                "complete-content storage refuses metadata-only application Commit"
                                    .into(),
                            ),
                            ..CommitReceipt::default()
                        }
                    } else {
                        self.apply_batch(&transaction, batch)?
                    }
                }
                EntryPayload::Normal(ReplicaCommand::ContentApplication {
                    content_application,
                }) => {
                    let original =
                        read_meta::<Option<AcceptedContentState>>(&transaction, "content_state")?
                            .flatten();
                    let reply = self.apply_content_batch(
                        &transaction,
                        content_application,
                        admission.as_ref(),
                    )?;
                    if let Some(crate::content_store::ProducerAdmission::New(permit)) = admission {
                        view_permits.push(*permit);
                    }
                    if reply.accepted
                        && let Some(original) = original
                    {
                        replaced_views.push(original);
                    }
                    reply
                }
                EntryPayload::Normal(ReplicaCommand::Certificate { certificate }) => {
                    let mut trust: CertificateTrust = read_meta(&transaction, "certificate_trust")?
                        .ok_or_else(|| io_error("legacy authority refuses certificate commands"))?;
                    match trust.apply(certificate.clone()) {
                        Ok(receipt) => {
                            write_meta(&transaction, "certificate_trust", &trust)?;
                            CommitReceipt {
                                accepted: true,
                                certificate: Some(receipt),
                                ..CommitReceipt::default()
                            }
                        }
                        Err(error) => CommitReceipt {
                            rejection: Some(error),
                            ..CommitReceipt::default()
                        },
                    }
                }
            };
            if !retained_view {
                write_meta(&transaction, "applied", &entry.log_id)?;
            }
            replies.push(receipt);
        }
        transaction.commit().map_err(io_error)?;
        if self.has_producer_journal() {
            self.complete_view_producers(&connection, view_permits)?;
            return Ok(replies);
        }
        if !replaced_views.is_empty()
            && let Some(current) =
                read_meta::<Option<AcceptedContentState>>(&connection, "content_state")?.flatten()
        {
            for previous in replaced_views {
                if previous.batch_digest != current.batch_digest {
                    let pins = self.content_references(
                        ReplicaContentReferenceKind::View,
                        &format!("view:{}", previous.batch_digest),
                        &previous.batch_digest,
                        &previous.inputs,
                    )?;
                    self.release_content(&pins, &current.batch_digest)?;
                }
            }
        }
        Ok(replies)
    }
}
