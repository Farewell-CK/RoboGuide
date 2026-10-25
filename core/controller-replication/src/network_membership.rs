//! Original bounded complete-content voter admission before any joint-consensus proposal.

use crate::*;
use content_store::ContentBudget;

impl ReplicaNode {
    /// Owns serialization through the actual operation; caller expiry never cancels queued Raft work.
    pub(crate) async fn run_content_transition<T, F>(
        &self,
        clock: ContentBudget,
        work: F,
    ) -> Result<T, String>
    where
        T: Send + 'static,
        F: std::future::Future<Output = Result<T, String>> + Send + 'static,
    {
        let permit = tokio::time::timeout(
            Duration::from_millis(clock.remaining_ms()?),
            self.content_transition.clone().acquire_owned(),
        )
        .await
        .map_err(|_| "original complete-content transition admission expired".to_string())?
        .map_err(|error| error.to_string())?;
        clock.check()?;
        let original_clock = clock.clone();
        let owned = tokio::spawn(async move {
            let _permit = permit;
            original_clock.check()?;
            work.await
        });
        tokio::time::timeout(Duration::from_millis(clock.remaining_ms()?), owned)
            .await
            .map_err(|_| "original complete-content transition outcome is uncertain".to_string())?
            .map_err(|error| error.to_string())?
    }

    /// Serializes learner catch-up, complete byte admission and the actual joint-consensus result.
    pub(crate) async fn change_content_membership(
        &self,
        epoch: ControllerEpoch,
        voters: BTreeSet<u64>,
        retain_removed_as_learners: bool,
        permission: crate::content_store::StreamPermission,
    ) -> Result<(), String> {
        let clock = ContentBudget::issue(30000)?;
        let original_clock = clock.clone();
        let node = self.clone();
        self.run_content_transition(clock, async move {
            permission()?;
            node.check_epoch(epoch).await?;
            let original_voters = node
                .raft
                .metrics()
                .borrow()
                .membership_config
                .voter_ids()
                .collect::<BTreeSet<_>>();
            for member in voters.difference(&original_voters) {
                original_clock.check()?;
                let existing = node
                    .raft
                    .metrics()
                    .borrow()
                    .membership_config
                    .nodes()
                    .find_map(|(id, data)| (*id == *member).then_some(data.clone()));
                if let Some(existing) = existing {
                    if existing != node.configured_member(*member)? {
                        return Err("proposed learner original member/Root binding changed".into());
                    }
                } else {
                    node.add_learner_at_epoch(epoch, *member, permission.clone())
                        .await
                        .map_err(|error| format!("original learner catch-up refused: {error}"))?;
                }
            }
            node.require_voter_prefix(
                epoch,
                &original_voters,
                &voters,
                &original_clock,
                permission.clone(),
            )
            .await?;
            node.require_voter_content(
                &original_voters,
                &voters,
                &original_clock,
                permission.clone(),
            )
            .await
            .map_err(|error| format!("complete-content voter admission refused: {error}"))?;
            original_clock.check()?;
            node.check_epoch(epoch).await?;
            original_clock.check()?;
            permission()?;
            node.raft
                .change_membership(voters, retain_removed_as_learners)
                .await
                .map_err(|error| format!("original joint consensus result: {error}"))?;
            original_clock.check()?;
            permission()?;
            node.check_epoch(epoch).await
        })
        .await
    }

    /// Requires each newly promoted voter to acknowledge the original applied Raft prefix first.
    /// A matching unchanged application view alone cannot prove membership/Snapshot catch-up.
    async fn require_voter_prefix(
        &self,
        epoch: ControllerEpoch,
        original_voters: &BTreeSet<u64>,
        voters: &BTreeSet<u64>,
        clock: &ContentBudget,
        permission: crate::content_store::StreamPermission,
    ) -> Result<(), String> {
        let original = self.raft.metrics().borrow().last_applied;
        let Some(cut) = original else {
            return Ok(());
        };
        loop {
            clock.check()?;
            permission()?;
            if self.current_epoch() != Some(epoch) {
                return Err("new-voter original prefix source epoch changed".into());
            }
            let metrics = self.raft.metrics().borrow().clone();
            if voters.difference(original_voters).all(|member| {
                metrics
                    .replication
                    .as_ref()
                    .and_then(|members| members.get(member))
                    .copied()
                    .flatten()
                    .is_some_and(|matched| matched.index >= cut.index)
            }) {
                return Ok(());
            }
            tokio::time::sleep(Duration::from_millis(clock.remaining_ms()?.min(25))).await;
        }
    }

    /// Proves the original current quorum and every proposed learner against the same exact bytes.
    async fn require_voter_content(
        &self,
        original_voters: &BTreeSet<u64>,
        voters: &BTreeSet<u64>,
        clock: &ContentBudget,
        caller: crate::content_store::StreamPermission,
    ) -> Result<(), String> {
        clock.check()?;
        caller()?;
        let actual = self
            .raft
            .metrics()
            .borrow()
            .membership_config
            .voter_ids()
            .collect::<BTreeSet<_>>();
        if &actual != original_voters {
            return Err("original voting set changed before content admission".into());
        }
        self.require_initialization_members(
            voters.clone(),
            self.current_epoch()
                .ok_or("voter contract requires the current leader")?,
            clock,
            caller.clone(),
        )
        .await?;
        let view = self.store.network_view().await?;
        if view.revision == 0
            && view.checkpoint.is_none()
            && view.content.is_none()
            && view.history_anchor.is_none()
            && view.events.is_empty()
            && view.commands.is_empty()
        {
            return clock.check().map(|_| ());
        }
        let target = self.accepted_content_target().await?;
        let original_trust = self.network_target_authority(&target)?.digest();
        let original_clock = clock.clone();
        let witness = caller.clone();
        let permission = Arc::new(move || {
            original_clock.check()?;
            witness()
        });
        let mut complete = self
            .content_quorum_checked(target.clone(), permission, clock.clone())
            .await?;
        clock.check()?;
        let mut observations = tokio::task::JoinSet::new();
        for member in voters.difference(original_voters).copied() {
            let node = self.clone();
            let target = target.clone();
            observations.spawn(async move {
                let observation = transport::observe_content_target(&node, member, target).await?;
                Ok::<_, String>((member, observation))
            });
        }
        while let Some(result) = observations.join_next().await {
            let (member, observation) = result.map_err(|error| error.to_string())??;
            clock.check()?;
            caller()?;
            let owner = target
                .membership
                .nodes()
                .find_map(|(id, node)| (*id == member).then_some(&node.content_store_owner))
                .and_then(Option::as_ref)
                .ok_or("proposed voter has no original committed Root owner")?;
            let expected = MemberContentObservation {
                profile_identity: target.profile_identity.clone(),
                member,
                store_owner: owner.clone(),
                epoch: target.epoch,
                revision: target.revision,
                checkpoint_digest: target.checkpoint_digest.clone(),
                batch_digest: target.batch_digest.clone(),
                membership: target.membership.clone(),
                inputs: target.inputs.clone(),
            };
            if observation != expected {
                return Err("actual new-voter metadata/Root/byte observation differs".into());
            }
            complete.insert(member);
        }
        if voters.intersection(&complete).count() <= voters.len() / 2 {
            return Err("proposed voters lack an actual complete content majority".into());
        }
        self.store
            .check_committed_content_target(target.clone())
            .await?;
        self.check_epoch(target.epoch).await?;
        clock.check()?;
        caller()?;
        if self.network_target_authority(&target)?.digest() != original_trust {
            return Err("original content admission trust changed before joint consensus".into());
        }
        Ok(())
    }
}
