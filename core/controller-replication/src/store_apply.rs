//! Exact actual application transaction; original view permits span commit/rollback and publication.

use super::*;

impl SqliteRaftStore {
    /// Applies actual original entries inside one parent transaction; caller retains managed queue ownership.
    pub(super) fn apply_entries(
        &self,
        entries: Vec<Entry<ControllerRaftConfig>>,
    ) -> Result<Vec<CommitReceipt>, StorageError<u64>> {
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
