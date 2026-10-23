//! Original current consensus/content targets; live checks never come from serialized success flags.

use crate::*;
use sha2::Digest as _;

#[cfg(test)]
#[path = "network_content_tests.rs"]
mod tests;

impl ReplicaNode {
    /// Builds a fresh observation target over the exact original accepted candidate without rewriting it.
    pub async fn accepted_content_target(&self) -> Result<ContentQuorumTarget, String> {
        let epoch = self
            .current_epoch()
            .ok_or("accepted content observation requires the current leader")?;
        self.check_epoch(epoch).await?;
        let view = self.store.network_view().await?;
        let accepted = view
            .content
            .as_ref()
            .ok_or("original complete accepted candidate is unavailable")?;
        let (_, membership) = self.store.try_network_authority()?;
        let mut owners = BTreeMap::new();
        for voter in membership.voter_ids() {
            owners.insert(
                voter,
                membership
                    .nodes()
                    .find_map(|(id, node)| {
                        (*id == voter)
                            .then_some(node.content_store_owner.clone())
                            .flatten()
                    })
                    .ok_or("original committed voter root is absent")?,
            );
        }
        let target = ContentQuorumTarget {
            profile_identity: self.profile_identity(),
            epoch,
            revision: view.revision,
            checkpoint_digest: view
                .checkpoint
                .as_ref()
                .map(state::checkpoint_identity)
                .ok_or("original accepted checkpoint is absent")?,
            batch_digest: accepted.batch_digest.clone(),
            inputs: accepted.inputs.clone(),
            membership,
            member_stores: owners,
        };
        self.check_epoch(epoch).await?;
        self.network_target_authority(&target)?;
        Ok(target)
    }
    /// Stages only exact actual original Artifact bytes under the complete candidate and live authority.
    /// Caller chooses the immutable operation ID; an original Unknown never automatically recopies.
    pub async fn stage_content_from_source(
        &self,
        candidate: &ContentApplicationBatch,
        source: &artifact_store::FileSystemArtifactStore,
        operation_id: &str,
        duration_ms: u64,
    ) -> Result<ContentQuorumTarget, String> {
        let clock = crate::content_store::ContentBudget::issue(duration_ms)?;
        if operation_id.is_empty()
            || operation_id.len() > 128
            || operation_id.chars().any(char::is_control)
        {
            return Err("original content staging operation is invalid".into());
        }
        let actual = source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        if candidate.source_writer
            != (ApplicationArtifactWriter {
                owner: actual.owner_id.clone(),
                device: actual.lease_device,
                inode: actual.lease_inode,
            })
        {
            return Err(
                "actual original Artifact writer differs from the complete candidate".into(),
            );
        }
        let target = self.content_target_for(candidate).await?;
        let root = self.store.network_content_store()?;
        let capacity = root.stream_work_permit()?;
        let held = self.clone();
        let expected = target.clone();
        let check: crate::content_store::StreamPermission =
            Arc::new(move || held.network_target_authority(&expected).map(|_| ()));
        let source = source.clone();
        let original = target.clone();
        let command = operation_id.to_string();
        tokio::task::spawn_blocking(move || {
            let _capacity = capacity;
            clock.check()?;
            check()?;
            for input in &original.inputs {
                if input.source_owner == actual.owner_id {
                    let key = serde_json::to_vec(&(&command, &original, input))
                        .map_err(|error| error.to_string())?;
                    let command = format!("source:{:x}", sha2::Sha256::digest(key));
                    let result = root.copy_artifact_stream_at_budget(
                        &source,
                        original.clone(),
                        input,
                        &command,
                        clock.clone(),
                        check.clone(),
                    )?;
                    if result.receipt.is_none() {
                        return Err(
                            "original source staging remains Unknown; observe or explicitly repair"
                                .into(),
                        );
                    }
                }
            }
            read_target_bytes(&root, &original, &clock, &check)
        })
        .await
        .map_err(|error| error.to_string())??;
        self.network_target_authority(&target)?;
        Ok(target)
    }
    /// Commits a new complete original candidate only after actual staged bytes and same-voter proof.
    /// Original child ingress/Prepared-source permission is a separate composition contract.
    pub async fn commit_content(
        &self,
        candidate: ContentApplicationBatch,
    ) -> Result<CommitReceipt, String> {
        let clock = crate::content_store::ContentBudget::issue(300000)?;
        self.commit_content_checked(candidate, clock, Arc::new(|| Ok(())))
            .await
            .map(|(receipt, _)| receipt)
    }

    /// Carries one actual caller budget/witness through source, proposal and the exact full quorum.
    pub(crate) async fn commit_content_checked(
        &self,
        candidate: ContentApplicationBatch,
        clock: crate::content_store::ContentBudget,
        permission: crate::content_store::StreamPermission,
    ) -> Result<(CommitReceipt, BTreeSet<u64>), String> {
        clock.check()?;
        permission()?;
        let target = self.content_target_for(&candidate).await?;
        let root = self.store.network_content_store()?;
        let capacity = root.stream_work_permit()?;
        let node = self.clone();
        let original = target.clone();
        let work_clock = clock.clone();
        let caller = permission.clone();
        tokio::task::spawn_blocking(move || {
            let _capacity = capacity;
            work_clock.check()?;
            node.network_target_authority(&original)?;
            let held = node.clone();
            let expected = original.clone();
            let check: crate::content_store::StreamPermission = Arc::new(move || {
                caller()?;
                held.network_target_authority(&expected).map(|_| ())
            });
            read_target_bytes(&root, &original, &work_clock, &check)
        })
        .await
        .map_err(|error| error.to_string())??;
        let mut transfers = tokio::task::JoinSet::new();
        for receiver in target
            .membership
            .voter_ids()
            .filter(|member| *member != self.config.replica_id)
        {
            let node = self.clone();
            let target = target.clone();
            let inputs = target.inputs.clone();
            let transfer_clock = clock.clone();
            let caller = permission.clone();
            transfers.spawn(async move {
                for input in inputs {
                    let command = serde_json::to_vec(&(receiver, &target, &input))
                        .map_err(|error| error.to_string())?;
                    let command_id = format!("candidate:{:x}", sha2::Sha256::digest(command));
                    if let Err(error) = crate::transport::send_content_stream_checked(
                        &node,
                        receiver,
                        target.clone(),
                        input,
                        command_id,
                        transfer_clock.clone(),
                        caller.clone(),
                    )
                    .await
                    {
                        eprintln!("original content stream to member {receiver} refused: {error}");
                        return Err(error);
                    }
                }
                Ok::<(), String>(())
            });
        }
        while let Some(result) = transfers.join_next().await {
            let _outcome = result.map_err(|error| error.to_string())?;
            clock.check()?;
            permission()?;
        }
        clock.check()?;
        permission()?;
        self.check_epoch(target.epoch).await?;
        self.network_target_authority(&target)?;
        clock.check()?;
        permission()?;
        let committed = tokio::time::timeout(
            Duration::from_millis(clock.remaining_ms()?.min(5000)),
            self.raft.client_write(ReplicaCommand::ContentApplication {
                content_application: Box::new(candidate),
            }),
        )
        .await
        .map_err(|_| "original complete metadata Commit is uncertain".to_string())?
        .map_err(|error| error.to_string())?;
        if !committed.data.accepted {
            return Err(committed
                .data
                .rejection
                .unwrap_or("original complete metadata was rejected".into()));
        }
        clock.check()?;
        permission()?;
        let complete = self
            .content_quorum_checked(target, permission.clone(), clock.clone())
            .await?;
        clock.check()?;
        permission()?;
        Ok((committed.data, complete))
    }
    /// Independently reads this actual committed metadata and all actual bytes on an owned worker.
    pub async fn observe_content_target(
        &self,
        target: ContentQuorumTarget,
    ) -> Result<MemberContentObservation, String> {
        let held = self.clone();
        let original = target.clone();
        let check: crate::content_store::StreamPermission =
            Arc::new(move || held.network_target_authority(&original).map(|_| ()));
        self.observe_content_checked(target, check).await
    }

    /// Retains actual caller permission, root/capacity and original clock through complete byte reads.
    pub(crate) async fn observe_content_checked(
        &self,
        target: ContentQuorumTarget,
        check: crate::content_store::StreamPermission,
    ) -> Result<MemberContentObservation, String> {
        let clock = crate::content_store::ContentBudget::issue(15000)?;
        check()?;
        let root = self.store.network_content_store()?;
        let capacity = root.stream_work_permit()?;
        let node = self.clone();
        tokio::task::spawn_blocking(move || {
            let _capacity = capacity;
            clock.check()?;
            check()?;
            let before = node.store.accepted_view()?;
            let candidate = before
                .content
                .as_ref()
                .ok_or("actual member has no complete accepted metadata")?;
            if before.revision != target.revision
                || before
                    .checkpoint
                    .as_ref()
                    .map(state::checkpoint_identity)
                    .as_ref()
                    != Some(&target.checkpoint_digest)
                || candidate.batch_digest != target.batch_digest
                || candidate.inputs != target.inputs
            {
                return Err("actual member metadata differs from the exact original target".into());
            }
            read_target_bytes(&root, &target, &clock, &check)?;
            let after = node.store.accepted_view()?;
            if after.revision != before.revision
                || after.content != before.content
                || after.checkpoint != before.checkpoint
            {
                return Err(
                    "actual member metadata changed during original byte observation".into(),
                );
            }
            clock.check()?;
            check()?;
            Ok(MemberContentObservation {
                profile_identity: target.profile_identity,
                member: root.identity().member,
                store_owner: root.identity().store_owner.clone(),
                epoch: target.epoch,
                revision: target.revision,
                checkpoint_digest: target.checkpoint_digest,
                batch_digest: target.batch_digest,
                membership: target.membership,
                inputs: target.inputs,
            })
        })
        .await
        .map_err(|error| error.to_string())?
    }

    /// Requires actual complete metadata/byte observations from the same strict current/joint majorities.
    pub async fn content_quorum(
        &self,
        target: ContentQuorumTarget,
    ) -> Result<BTreeSet<u64>, String> {
        self.content_quorum_checked(
            target,
            Arc::new(|| Ok(())),
            crate::content_store::ContentBudget::issue(15000)?,
        )
        .await
    }

    /// Observes independent actual members concurrently inside the original caller clock and witness.
    async fn content_quorum_checked(
        &self,
        target: ContentQuorumTarget,
        permission: crate::content_store::StreamPermission,
        clock: crate::content_store::ContentBudget,
    ) -> Result<BTreeSet<u64>, String> {
        clock.check()?;
        permission()?;
        self.check_epoch(target.epoch).await?;
        self.network_target_authority(&target)?;
        let mut observed = Vec::new();
        let mut unavailable = Vec::new();
        let mut observations = tokio::task::JoinSet::new();
        for member in target.membership.voter_ids() {
            let node = self.clone();
            let target = target.clone();
            let caller = permission.clone();
            let original_clock = clock.clone();
            observations.spawn(async move {
                let result = async {
                    original_clock.check()?;
                    caller()?;
                    let observed = if member == node.config.replica_id {
                        node.observe_content_checked(target, caller.clone()).await
                    } else {
                        transport::observe_content_target(&node, member, target).await
                    };
                    original_clock.check()?;
                    caller()?;
                    observed
                }
                .await;
                (member, result)
            });
        }
        while let Some(result) = observations.join_next().await {
            let (member, result) = result.map_err(|error| error.to_string())?;
            match result {
                Ok(actual) => observed.push(actual),
                Err(error) => {
                    unavailable.push((member, error.chars().take(512).collect::<String>()))
                }
            }
        }
        self.check_epoch(target.epoch).await?;
        self.network_target_authority(&target)?;
        clock.check()?;
        permission()?;
        target
            .complete_members(&observed)
            .map_err(|error| format!("{error}; actual unavailable members: {unavailable:?}"))
    }
    /// Requires both actual committed membership and live Raft epoch before any bounded content I/O.
    pub(crate) fn network_target_authority(
        &self,
        target: &ContentQuorumTarget,
    ) -> Result<CertificateTrust, String> {
        target.validate()?;
        if !self.config.streams_content() || target.profile_identity != self.profile_identity() {
            return Err("original network target has another profile/generation".into());
        }
        let metrics = self.raft.metrics().borrow().clone();
        let (trust, committed) = self.store.try_network_authority()?;
        if metrics.current_term != target.epoch.term
            || metrics.current_leader != Some(target.epoch.replica_id)
            || metrics.membership_config.as_ref() != &target.membership
            || committed != target.membership
        {
            return Err("original network epoch or exact committed membership changed".into());
        }
        self.store
            .validate_content_membership(target.membership.membership())?;
        Ok(trust)
    }

    /// Builds retention context only after actual current quorum and complete original candidate checks.
    pub async fn content_target_for(
        &self,
        candidate: &ContentApplicationBatch,
    ) -> Result<ContentQuorumTarget, String> {
        let epoch = self
            .current_epoch()
            .ok_or("content preparation requires the current leader")?;
        self.check_epoch(epoch).await?;
        if candidate.epoch != epoch || !self.config.streams_content() {
            return Err("original content candidate has another leader epoch/generation".into());
        }
        let previous = self.store.network_view().await?;
        candidate.validate_against(&previous, &self.profile_identity())?;
        let (_, membership) = self.store.try_network_authority()?;
        let mut owners = BTreeMap::new();
        for voter in membership.voter_ids() {
            let owner = membership
                .nodes()
                .find_map(|(id, node)| {
                    (*id == voter)
                        .then_some(node.content_store_owner.clone())
                        .flatten()
                })
                .ok_or("original committed voter lacks its exact root")?;
            owners.insert(voter, owner);
        }
        let target = ContentQuorumTarget {
            profile_identity: self.profile_identity(),
            epoch,
            revision: previous
                .revision
                .checked_add(1)
                .ok_or("original application revision exhausted")?,
            checkpoint_digest: state::checkpoint_identity(&candidate.batch.checkpoint),
            batch_digest: candidate.identity()?,
            membership,
            member_stores: owners,
            inputs: candidate.inputs.clone(),
        };
        self.check_epoch(epoch).await?;
        self.network_target_authority(&target)?;
        Ok(target)
    }

    /// Streams one already owned qualified root file over actual mTLS; no application ACK is inferred.
    pub async fn stream_content_to(
        &self,
        receiver: u64,
        target: ContentQuorumTarget,
        content: ArtifactContentKey,
        command_id: String,
        duration_ms: u64,
    ) -> Result<ReplicaContentStreamStatus, String> {
        self.check_epoch(target.epoch).await?;
        self.network_target_authority(&target)?;
        if target.epoch.replica_id != self.config.replica_id
            || target.inputs.binary_search(&content).is_err()
            || receiver == self.config.replica_id
        {
            return Err(
                "original committed source/receiver/qualified input differs from its stream target"
                    .into(),
            );
        }
        transport::send_content_stream(self, receiver, target, content, command_id, duration_ms)
            .await
    }

    /// Routes only exact already-committed qualified bytes; this cannot stage a proposed application.
    pub async fn route_committed_content_to(
        &self,
        receiver: u64,
        target: ContentQuorumTarget,
        content: ArtifactContentKey,
        command_id: String,
        duration_ms: u64,
    ) -> Result<ReplicaContentStreamStatus, String> {
        self.network_target_authority(&target)?;
        if !target
            .membership
            .voter_ids()
            .any(|id| id == self.config.replica_id)
            || !target.membership.voter_ids().any(|id| id == receiver)
            || receiver == self.config.replica_id
            || target.inputs.binary_search(&content).is_err()
        {
            return Err("committed content route has another source/receiver/input".into());
        }
        self.store.require_committed_content_target(&target)?;
        crate::transport::send_committed_content_stream(
            self,
            receiver,
            target,
            content,
            command_id,
            duration_ms,
        )
        .await
    }
}

/// Reads complete actual qualified inputs under independent original pins and nonrenewing live permission.
fn read_target_bytes(
    root: &ReplicaContentStore,
    target: &ContentQuorumTarget,
    clock: &crate::content_store::ContentBudget,
    check: &crate::content_store::StreamPermission,
) -> Result<(), String> {
    let operation = crate::supervision::new_process_instance()?;
    for content in &target.inputs {
        clock.check()?;
        check()?;
        let input = serde_json::to_vec(content).map_err(|error| error.to_string())?;
        let reader_id = format!("observe:{operation}:{:x}", sha2::Sha256::digest(input));
        let scoped_clock = clock.clone();
        let permission = check.clone();
        let opening: crate::content_store::StreamPermission = Arc::new(move || {
            scoped_clock.check()?;
            permission()
        });
        let mut reader = root.open_reader_checked(
            content,
            &reader_id,
            &target.batch_digest,
            clock.duration_ms,
            opening,
        )?;
        let mut chunk = [0; crate::content_store::STREAM_CHUNK_BYTES];
        loop {
            clock.check()?;
            check()?;
            let count =
                std::io::Read::read(&mut reader, &mut chunk).map_err(|error| error.to_string())?;
            clock.check()?;
            check()?;
            if count == 0 {
                break;
            }
        }
        if !reader.finish()?.complete {
            return Err("actual original member reader did not verify complete bytes".into());
        }
    }
    Ok(())
}
