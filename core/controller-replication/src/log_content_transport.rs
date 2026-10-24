//! Exact retained Log catch-up over authenticated members and original bounded stream operations.

use super::*;
use crate::content_store::ContentBudget;
use openraft::LogId;

/// Original receiver admission outlives the async task while any actual blocking job still runs.
type CatchupScope = Arc<tokio::sync::OwnedSemaphorePermit>;

/// Derives one immutable source/receiver/Log/input operation; a new epoch cannot replace an old ID.
pub(super) fn operation(
    profile: &str,
    source: u64,
    receiver: u64,
    id: LogId<u64>,
    content: &ArtifactContentKey,
) -> Result<String, String> {
    Ok(format!(
        "historical-log:{:x}",
        Sha256::digest(
            serde_json::to_vec(&(profile, source, receiver, id, content,))
                .map_err(|e| e.to_string())?
        )
    ))
}

/// Retains actual Root work capacity inside each blocking SQL/hash task, even after waiter cancellation.
pub(super) async fn on_worker<T, F>(node: &ReplicaNode, work: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce(ReplicaNode) -> Result<T, String> + Send + 'static,
{
    on_worker_scoped(node, None, work).await
}

/// Retains the original background admission alongside actual Root work through waiter cancellation.
async fn on_worker_scoped<T, F>(
    node: &ReplicaNode,
    scope: Option<CatchupScope>,
    work: F,
) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce(ReplicaNode) -> Result<T, String> + Send + 'static,
{
    let root = node.store.network_content_store()?;
    let held = node.clone();
    root_work(root, scope, move || work(held)).await
}

/// Owns exact Root/work/admission lifetimes inside the real blocking task, even if its awaiter ends.
async fn root_work<T, F>(
    root: ReplicaContentStore,
    scope: Option<CatchupScope>,
    work: F,
) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, String> + Send + 'static,
{
    let capacity = root.stream_work_permit()?;
    tokio::task::spawn_blocking(move || {
        let _root = root;
        let _capacity = capacity;
        let _scope = scope;
        work()
    })
    .await
    .map_err(|e| e.to_string())?
}

impl ReplicaNode {
    /// Reads actual source Commit/Log/current membership; this description creates no byte permission.
    pub(crate) fn historical_log_target(
        &self,
        id: LogId<u64>,
    ) -> Result<ContentQuorumTarget, String> {
        let candidate = self.store.retained_log_candidate(id, true)?;
        let metrics = self.raft.metrics().borrow().clone();
        let epoch = ControllerEpoch {
            term: metrics.current_term,
            replica_id: metrics
                .current_leader
                .ok_or("historical source has no actual current leader")?,
        };
        let (_, membership) = self.store.try_network_authority()?;
        let member_stores = membership
            .voter_ids()
            .map(|voter| {
                membership
                    .nodes()
                    .find_map(|(id, data)| {
                        (*id == voter)
                            .then_some(data.content_store_owner.clone())
                            .flatten()
                    })
                    .map(|owner| (voter, owner))
                    .ok_or("historical voter Root owner is absent".to_string())
            })
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        let target = ContentQuorumTarget {
            profile_identity: self.profile_identity(),
            epoch,
            revision: self.store.read_network_snapshot(|sql| {
                crate::store::read_meta(sql, "revision")
                    .map_err(|e| e.to_string())?
                    .ok_or("historical source revision is absent".into())
            })?,
            checkpoint_digest: state::checkpoint_identity(&candidate.batch.checkpoint),
            batch_digest: candidate.identity()?,
            membership,
            member_stores,
            inputs: candidate.inputs,
        };
        self.network_target_authority(&target)?;
        self.store
            .require_historical_log_target(id, &target, true)?;
        Ok(target)
    }

    /// Executes only one exact original source operation; receiver independently checks its durable Log.
    pub(crate) async fn route_historical_log_to(
        &self,
        receiver: u64,
        id: LogId<u64>,
        target: ContentQuorumTarget,
        content: ArtifactContentKey,
        command_id: String,
        clock: ContentBudget,
    ) -> Result<ReplicaContentStreamStatus, String> {
        clock.check()?;
        self.network_target_authority(&target)?;
        if !target
            .membership
            .voter_ids()
            .any(|member| member == self.config.replica_id)
            || !target
                .membership
                .voter_ids()
                .any(|member| member == receiver)
            || receiver == self.config.replica_id
            || target.inputs.binary_search(&content).is_err()
            || command_id
                != operation(
                    &self.profile_identity(),
                    self.config.replica_id,
                    receiver,
                    id,
                    &content,
                )?
        {
            return Err("historical route has another exact source/receiver/input".into());
        }
        let original = target.clone();
        on_worker(self, move |source| {
            source.store.retained_log_candidate(id, true)?;
            source
                .store
                .require_historical_log_target(id, &original, true)
        })
        .await?;
        send_historical_log_stream(self, receiver, target, content, command_id, clock, id).await
    }

    /// Explicitly catches up a real retained Log, without creating or committing an application.
    pub async fn recover_historical_log_from(
        &self,
        source: u64,
        id: LogId<u64>,
        duration_ms: u64,
    ) -> Result<Vec<ReplicaContentStreamStatus>, String> {
        self.recover_historical_log_at(source, id, ContentBudget::issue(duration_ms)?, None)
            .await
    }

    /// Carries one original ingress budget across discovery and all real source/receiver requests.
    async fn recover_historical_log_at(
        &self,
        source: u64,
        id: LogId<u64>,
        clock: ContentBudget,
        scope: Option<CatchupScope>,
    ) -> Result<Vec<ReplicaContentStreamStatus>, String> {
        clock.check()?;
        if source == self.config.replica_id
            || !self
                .raft
                .metrics()
                .borrow()
                .membership_config
                .voter_ids()
                .any(|id| id == source)
        {
            return Err("historical source is not another current voter".into());
        }
        let missing = on_worker_scoped(self, scope.clone(), move |held| {
            let candidate = held.store.retained_log_candidate(id, false)?;
            let root = held.store.network_content_store()?;
            candidate
                .inputs
                .into_iter()
                .filter_map(|key| match root.retained_content_is_available(&key) {
                    Ok(true) => None,
                    Ok(false) => Some(Ok(key)),
                    Err(error) => Some(Err(error)),
                })
                .collect::<Result<Vec<_>, String>>()
        })
        .await?;
        if missing.is_empty() {
            return Ok(Vec::new());
        }
        let target = match node_network(self, source)?
            .exchange(
                Command::HistoricalLogTargetV1 { log_id: id },
                RPCOption::new(Duration::from_millis(clock.remaining_ms()?.min(15000))),
            )
            .await
            .map_err(|e| e.to_string())?
        {
            Reply::HistoricalLogTarget(result) => result?,
            Reply::Rejected(error) => return Err(error),
            _ => return Err("historical source returned no exact Log target".into()),
        };
        self.network_target_authority(&target)?;
        let original = target.clone();
        on_worker_scoped(self, scope.clone(), move |held| {
            held.store
                .require_historical_log_target(id, &original, false)
        })
        .await?;
        let mut results = Vec::new();
        for content in missing {
            clock.check()?;
            let command_id = operation(
                &self.profile_identity(),
                source,
                self.config.replica_id,
                id,
                &content,
            )?;
            let original_id = command_id.clone();
            let original = on_worker_scoped(self, scope.clone(), move |held| {
                held.store
                    .network_content_store()?
                    .stream_status(&original_id)
            })
            .await?;
            if let Some(original) = original {
                return Err(format!(
                    "original historical stream remains retained; no repeated effect: {}",
                    original.command_id
                ));
            }
            match node_network(self, source)?
                .exchange(
                    Command::RouteHistoricalLogV1 {
                        log_id: id,
                        target: Box::new(target.clone()),
                        content: content.clone(),
                        command_id: command_id.clone(),
                        budget_ms: clock.remaining_ms()?.min(15000),
                    },
                    RPCOption::new(Duration::from_millis(clock.remaining_ms()?.min(15000))),
                )
                .await
                .map_err(|e| e.to_string())?
            {
                Reply::HistoricalLogRouted(result) => {
                    result?;
                }
                Reply::Rejected(error) => return Err(error),
                _ => return Err("actual historical stream outcome is unavailable".into()),
            }
            clock.check()?;
            self.network_target_authority(&target)?;
            let expected = content.clone();
            let actual = on_worker_scoped(self, scope.clone(), move |held| {
                let root = held.store.network_content_store()?;
                let original = root
                    .stream_status(&command_id)?
                    .ok_or("original local historical receipt is absent")?;
                if original.receipt.is_none() || !root.retained_content_is_available(&expected)? {
                    return Err("actual historical receive remains Unknown".into());
                }
                Ok::<_, String>(original)
            })
            .await?;
            results.push(actual);
        }
        Ok(results)
    }

    /// Owns one bounded actual task independently of metadata ACK; active work never admits a duplicate.
    pub(crate) fn queue_retained_log_catchup(
        &self,
        source: u64,
        through: Option<LogId<u64>>,
        clock: ContentBudget,
    ) -> Result<bool, String> {
        let Some(cut) = through else {
            return Ok(false);
        };
        if self
            .raft
            .metrics()
            .borrow()
            .last_applied
            .is_some_and(|applied| applied.index >= cut.index)
        {
            return Ok(false);
        }
        clock.check()?;
        let Some(scope) = self
            .store
            .network_content_store()?
            .try_historical_catchup()?
        else {
            return Ok(false);
        };
        let remaining = clock.remaining_ms()?;
        let original = self.clone();
        let scope = Arc::new(scope);
        tokio::spawn(async move {
            let result = tokio::time::timeout(
                Duration::from_millis(remaining),
                original.catch_up_retained_logs(source, through, clock, scope),
            )
            .await;
            let error = match result {
                Ok(Ok(())) => None,
                Ok(Err(error)) => Some(error),
                Err(_) => Some("original retained Log catch-up budget expired".into()),
            };
            if let Some(error) = error {
                eprintln!(
                    "original retained Log content remains unavailable: {}",
                    error.chars().take(512).collect::<String>()
                );
            }
        });
        Ok(true)
    }

    /// Uses only the actual authenticated Append sender and already-persisted prefix in its first scope.
    async fn catch_up_retained_logs(
        &self,
        source: u64,
        through: Option<LogId<u64>>,
        clock: ContentBudget,
        scope: CatchupScope,
    ) -> Result<(), String> {
        let logs = on_worker_scoped(self, Some(scope.clone()), move |held| {
            held.store.pending_content_logs(through)
        })
        .await?;
        for id in logs {
            clock.check()?;
            self.recover_historical_log_at(source, id, clock.clone(), Some(scope.clone()))
                .await?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Cancelling the real async awaiter preserves the original gate/writer until actual work ends.
    #[tokio::test(flavor = "multi_thread", worker_threads = 1)]
    async fn cancelled_catchup_work_retains_original_admission_and_root_writer() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("actual-root");
        let profile = "a".repeat(64);
        let root = ReplicaContentStore::create_with_streams(&path, &profile, 1).unwrap();
        let scope = Arc::new(root.try_historical_catchup().unwrap().unwrap());
        let (started, observed) = tokio::sync::oneshot::channel();
        let (release, blocked) = std::sync::mpsc::channel();
        let owned = root.clone();
        let task = tokio::spawn(async move {
            root_work(owned, Some(scope), move || {
                let _ = started.send(());
                blocked.recv().map_err(|error| error.to_string())
            })
            .await
        });
        observed.await.unwrap();
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        assert!(root.try_historical_catchup().unwrap().is_none());
        drop(root);
        assert!(ReplicaContentStore::open_with_streams(&path, &profile, 1).is_err());
        release.send(()).unwrap();
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                if let Ok(reopened) = ReplicaContentStore::open_with_streams(&path, &profile, 1) {
                    assert!(reopened.try_historical_catchup().unwrap().is_some());
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
    }
}
