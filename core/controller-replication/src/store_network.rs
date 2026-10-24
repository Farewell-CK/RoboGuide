//! Explicit network content parent validation; a direct Root4 handle never upgrades legacy authority.

use super::*;
use std::os::unix::fs::MetadataExt;

#[cfg(test)]
#[path = "store_network_tests.rs"]
mod tests;

impl SqliteRaftStore {
    /// Reads only a committed original WAL snapshot without queuing consensus behind application SQL.
    pub(super) fn read_consensus_snapshot<T>(
        &self,
        read: impl FnOnce(&Connection) -> Result<T, StorageError<u64>>,
    ) -> Result<T, StorageError<u64>> {
        if self.network_content {
            return self
                .read_network_snapshot(|connection| {
                    read(connection).map_err(|error| error.to_string())
                })
                .map_err(io_error);
        }
        let connection = self.connection.lock().map_err(io_error)?;
        read(&connection)
    }

    /// Rechecks actual current retention/publication without renewing an original read on unrelated commits.
    pub(crate) fn require_current_content_read(
        &self,
        original: &ContentQuorumTarget,
        key: &ArtifactContentKey,
        published: bool,
        recovery: Option<&domain::ArtifactUploadRecovery>,
    ) -> Result<(), String> {
        self.read_network_snapshot(|connection| {
            let current = read_view(connection).map_err(|error| error.to_string())?;
            let content = current
                .content
                .as_ref()
                .ok_or("current qualified content metadata unavailable")?;
            if current.revision < original.revision
                || content.profile_identity != original.profile_identity
                || content.inputs.binary_search(key).is_err()
                || content.identity()? != content.batch_digest
                || current.checkpoint.as_ref() != Some(&content.batch.checkpoint)
            {
                return Err(
                    "original qualified read is no longer retained by current complete metadata"
                        .into(),
                );
            }
            if let Some(original) = recovery {
                let (_, budget) = crate::controller_ingress::recovery::evidence(original)?;
                budget.check()?;
                if crate::controller_ingress::recovery::pending(&current, &original.command_id)?
                    != *original
                {
                    return Err("original recovery permission changed during payload read".into());
                }
            }
            if published && content.published_content(&current.events, &key.content_digest)? != *key
            {
                return Err("original published content changed its qualified namespace".into());
            }
            Ok(())
        })
    }
    /// Requires the actual committed complete target; membership alone never authorizes routed bytes.
    pub(crate) async fn check_committed_content_target(
        &self,
        target: ContentQuorumTarget,
    ) -> Result<(), String> {
        self.run_storage_work(move |store| {
            store
                .require_committed_content_target(&target)
                .map_err(io_error)
        })
        .await
        .map_err(|error| error.to_string())
    }

    /// Reads the exact committed candidate on the owning worker without changing original authority.
    pub(crate) fn require_committed_content_target(
        &self,
        target: &ContentQuorumTarget,
    ) -> Result<(), String> {
        self.read_network_snapshot(|connection| {
            let revision: u64 = read_meta(connection, "revision")
                .map_err(|error| error.to_string())?
                .ok_or("committed content revision is unavailable")?;
            let checkpoint: state::PersistedCheckpoint = read_meta(connection, "checkpoint")
                .map_err(|error| error.to_string())?
                .ok_or("committed content checkpoint is unavailable")?;
            let content: AcceptedContentState = read_meta(connection, "content_state")
                .map_err(|error| error.to_string())?
                .ok_or("committed complete candidate is unavailable")?;
            if revision != target.revision
                || state::checkpoint_identity(&checkpoint) != target.checkpoint_digest
                || content.batch_digest != target.batch_digest
                || content.identity()? != target.batch_digest
                || content.inputs != target.inputs
                || content.batch.checkpoint != checkpoint
            {
                return Err(
                    "actual committed complete content target differs from original route".into(),
                );
            }
            Ok(())
        })
    }
    /// Checks current committed source fences without blocking network tasks behind application SQL.
    pub(crate) fn connection_source_fence(&self, member: u64) -> Result<(), String> {
        let check = |connection: &Connection| {
            if read_meta::<bool>(connection, crate::supervision::LEGACY_CONTINUITY_KEY)
                .map_err(|error| error.to_string())?
                != Some(false)
            {
                return Err(
                    "legacy Controller process continuity requires independent migration evidence"
                        .into(),
                );
            }
            if read_leader_transfer_history(connection)?
                .last()
                .is_some_and(|record| {
                    record.source_epoch.replica_id == member
                        && !matches!(record.state.as_str(), "completed" | "rejected")
                })
            {
                return Err(
                    "original source Controller is fenced pending leader transfer reconciliation"
                        .into(),
                );
            }
            Ok(())
        };
        if self.network_content {
            self.read_network_snapshot(check)
        } else {
            let connection = self.connection.lock().map_err(|error| error.to_string())?;
            check(&connection)
        }
    }
    /// Reads committed actual network trust without blocking heartbeat TLS behind application SQL work.
    pub(crate) fn connection_certificate_trust(&self) -> Result<CertificateTrust, String> {
        if !self.network_content {
            return self
                .certificate_trust()?
                .ok_or("original certificate authority is missing".into());
        }
        self.read_network_snapshot(|connection| {
            let trust: CertificateTrust = read_meta(connection, "certificate_trust")
                .map_err(|error| error.to_string())?
                .ok_or("original network certificate authority is missing")?;
            trust.validate()?;
            Ok(trust)
        })
    }
    /// Reads an actual committed WAL snapshot under original inode leases without parent/root lock inversion.
    pub(crate) fn try_network_authority(
        &self,
    ) -> Result<(CertificateTrust, StoredMembership<u64, ReplicaMember>), String> {
        self.read_network_snapshot(|transaction| {
            let trust: CertificateTrust = read_meta(transaction, "certificate_trust")
                .map_err(|error| error.to_string())?
                .ok_or("original network certificate authority is missing")?;
            trust.validate()?;
            let membership = read_meta(transaction, "membership")
                .map_err(|error| error.to_string())?
                .ok_or("original committed network membership is unavailable")?;
            Ok((trust, membership))
        })
    }

    /// Reads one committed original-inode WAL snapshot without acquiring the live parent mutex.
    pub(crate) fn read_network_snapshot<T>(
        &self,
        read: impl FnOnce(&Connection) -> Result<T, String>,
    ) -> Result<T, String> {
        if !self.network_content {
            return Err("original network trust requires its explicit parent generation".into());
        }
        let path = &self._writer.path;
        let backing = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
        let identity = self._writer.identity()?;
        if !backing.is_file() || (backing.dev(), backing.ino()) != identity {
            return Err("original network SQL backing changed before readonly authority".into());
        }
        let connection = Connection::open_with_flags(
            path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|error| error.to_string())?;
        connection
            .busy_timeout(Duration::from_millis(100))
            .map_err(|error| error.to_string())?;
        let transaction = connection
            .unchecked_transaction()
            .map_err(|error| error.to_string())?;
        let version: i64 = transaction
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .map_err(|error| error.to_string())?;
        if version
            != if self.initialization_content {
                INITIALIZATION_CONTENT_VERSION
            } else if self.controller_content {
                CONTROLLER_CONTENT_VERSION
            } else {
                NETWORK_CONTENT_VERSION
            }
            || read_meta::<String>(&transaction, NETWORK_CONTENT_KEY)
                .map_err(|error| error.to_string())?
                .as_deref()
                != Some(NETWORK_CONTENT_SCHEMA)
            || read_meta::<(String, String)>(&transaction, "identity")
                .map_err(|error| error.to_string())?
                != Some((self.profile.clone(), self.application_schema.clone()))
        {
            return Err("original readonly network generation/profile is unavailable".into());
        }
        if self.controller_content {
            crate::controller_ingress::require_marker(&transaction)?;
        }
        if self.initialization_content
            && read_meta::<Option<ContentInitializationPolicy>>(
                &transaction,
                INITIALIZATION_POLICY_KEY,
            )
            .map_err(|error| error.to_string())?
                != Some(self.initialization_policy.as_deref().cloned())
        {
            return Err("original readonly initialization policy is missing or changed".into());
        }
        let result = read(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        let backing = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
        if !backing.is_file() || (backing.dev(), backing.ino()) != identity {
            return Err("original network SQL backing changed during readonly authority".into());
        }
        Ok(result)
    }

    /// Captures an original complete committed view on an owned bounded worker, retaining actual leases.
    pub(crate) async fn network_view(&self) -> Result<ReplicaView, String> {
        self.run_storage_work(|store| {
            store.network_content_store().map_err(io_error)?;
            store.accepted_view().map_err(io_error)
        })
        .await
        .map_err(|error| error.to_string())
    }
    /// Checks all exact original member bindings and the actual local root before any parent effect.
    pub(crate) fn validate_content_membership(
        &self,
        membership: &openraft::Membership<u64, ReplicaMember>,
    ) -> Result<(), String> {
        if !self.network_content {
            return crate::member::require_legacy_membership(membership);
        }
        let root = self
            .content
            .as_ref()
            .ok_or("network content parent lost its actual original root")?;
        if !root.streams_enabled() {
            return Err("network content parent lost its explicit stream generation".into());
        }
        let mut addresses = BTreeSet::new();
        let mut owners = BTreeSet::new();
        let mut members = BTreeSet::new();
        for (id, member) in membership.nodes() {
            member.validate_content_binding()?;
            if *id == 0
                || !addresses.insert(&member.addr)
                || !owners.insert(&member.content_store_owner)
                || !members.insert(*id)
                || (*id == root.identity().member
                    && member.content_store_owner.as_ref() != Some(&root.identity().store_owner))
            {
                return Err(
                    "network membership duplicates or changes an actual root binding".into(),
                );
            }
        }
        if members.len() > 9
            || membership
                .voter_ids()
                .any(|voter| !members.contains(&voter))
            || membership.get_joint_config().len() > 2
            || membership.get_joint_config().iter().any(BTreeSet::is_empty)
        {
            return Err("network content membership lacks bounded complete voter data".into());
        }
        Ok(())
    }

    /// Returns the same managed actual root, preserving its original leases inside owned network work.
    pub(crate) fn network_content_store(&self) -> Result<ReplicaContentStore, String> {
        if !self.network_content {
            return Err("metadata/direct profile has no network content authority".into());
        }
        self.content
            .as_ref()
            .filter(|root| root.streams_enabled())
            .cloned()
            .ok_or("original network content root is unavailable".into())
    }

    /// Allows only this exact new empty generation to bootstrap actual certificate authority.
    pub(super) fn network_genesis(&self, connection: &Connection) -> Result<bool, String> {
        if !self.network_content {
            return Ok(false);
        }
        if self.controller_content && !crate::controller_ingress::empty_storage(connection)? {
            return Err(
                "original Controller ingress history prevents certificate bootstrap".into(),
            );
        }
        if self.initialization_content {
            crate::source_initialization::validate_storage(
                connection,
                self.content.as_ref().map(|root| root.identity()),
                &self.profile,
                self.initialization_policy.as_deref(),
                true,
            )?;
            if read_meta::<Vec<String>>(connection, crate::source_initialization::HEAD_KEY)
                .map_err(|error| error.to_string())?
                != Some(Vec::new())
            {
                return Err(
                    "original initialization history prevents certificate bootstrap".into(),
                );
            }
        }
        if read_meta::<String>(connection, NETWORK_CONTENT_KEY)
            .map_err(|error| error.to_string())?
            .as_deref()
            != Some(NETWORK_CONTENT_SCHEMA)
            || read_meta::<Option<AcceptedContentState>>(connection, "content_state")
                .map_err(|error| error.to_string())?
                != Some(None)
            || producers::read_heads(connection).map_err(|error| error.to_string())?
                != producers::ProducerHeads::default()
        {
            return Err(
                "original network certificate genesis is missing or no longer empty".into(),
            );
        }
        let rows: u64 = connection
            .query_row(
                "SELECT (SELECT COUNT(*) FROM replica_v1_events)+\
                (SELECT COUNT(*) FROM replica_v1_commands)+(SELECT COUNT(*) FROM replica_v1_snapshot)",
                [],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        Ok(rows == 0)
    }
}
