//! Durable Raft prefix ACK is separate from conservative original Root reference retirement.

use super::*;

/// One bounded coalesced queue shared by all actual original store clones.
#[derive(Default)]
pub(super) struct PrefixCleanup {
    /// Local work observation only; no authoritative receipt or operation clock is stored here.
    state: Mutex<CleanupState>,
    /// Wakes high-level maintenance after the actual owned job finishes or fails.
    changed: tokio::sync::Notify,
}

/// Process-local scheduling state; retained SQL remains the sole original evidence.
#[derive(Default)]
struct CleanupState {
    /// Exactly one queued/running owner may retain this Root/parent cleanup responsibility.
    running: bool,
    /// A later durable prefix arrived during work and needs another bounded complete observation.
    dirty: bool,
    /// Actual last job failure; missing pins are never inferred from a scheduling flag.
    error: Option<String>,
}

impl SqliteRaftStore {
    /// Queues at most one owned cleanup after the actual durable metadata Commit and Raft ACK boundary.
    pub(super) fn queue_prefix_cleanup(&self) {
        {
            let Ok(mut state) = self.prefix_cleanup.state.lock() else {
                eprintln!(
                    "original prefix cleanup scheduling is unavailable; pins remain retained"
                );
                return;
            };
            state.dirty = true;
            if state.running {
                return;
            }
            state.running = true;
            state.error = None;
        }
        let original = self.clone();
        tokio::spawn(async move {
            loop {
                if let Ok(mut state) = original.prefix_cleanup.state.lock() {
                    state.dirty = false;
                }
                let work = original
                    .run_storage_work(|store| store.reconcile_purged_prefix().map_err(io_error))
                    .await
                    .map_err(|error| error.to_string());
                let again = match original.prefix_cleanup.state.lock() {
                    Ok(mut state) => {
                        if let Err(error) = work {
                            eprintln!("original prefix references remain retained: {error}");
                            state.error = Some(error);
                            state.running = false;
                            false
                        } else if state.dirty {
                            true
                        } else {
                            state.running = false;
                            false
                        }
                    }
                    Err(_) => false,
                };
                original.prefix_cleanup.changed.notify_waiters();
                if !again {
                    break;
                }
            }
        });
    }

    /// Waits for actual owned cleanup; a failed observation grants no Root retirement or renewed budget.
    pub(crate) async fn wait_for_prefix_cleanup(&self) -> Result<(), String> {
        loop {
            let changed = self.prefix_cleanup.changed.notified();
            {
                let state = self
                    .prefix_cleanup
                    .state
                    .lock()
                    .map_err(|_| "original prefix cleanup observation is unavailable")?;
                if !state.running {
                    return state.error.clone().map_or(Ok(()), Err);
                }
            }
            changed.await;
        }
    }

    /// Freezes complete original prefix evidence, then retires only irreversibly purged Log consumers.
    fn reconcile_purged_prefix(&self) -> Result<(), String> {
        let observations = {
            let connection = self.connection.lock().map_err(|error| error.to_string())?;
            self.validate_content_producer_heads(&connection)?;
            let cut = read_meta::<LogId<u64>>(&connection, "purged")
                .map_err(|error| error.to_string())?
                .ok_or("original prefix cleanup lacks its durable purged cut")?;
            let parent = self._writer.identity()?;
            let mut observations = Vec::new();
            for original in self.parent_producers().map_err(|error| error.to_string())? {
                let source: ReplicaContentProducerSource =
                    serde_json::from_value(original.original["intent"]["source"].clone())
                        .map_err(|error| error.to_string())?;
                if source.kind != ReplicaContentProducerKind::Log {
                    continue;
                }
                let id: LogId<u64> =
                    serde_json::from_str(&source.position).map_err(|error| error.to_string())?;
                if id.index > cut.index {
                    continue;
                }
                let proof = format!(
                    "{:x}",
                    Sha256::digest(
                        serde_json::to_vec(&(
                            "roboguide.purged-producer-observation/v0.1",
                            &self.profile,
                            parent,
                            cut,
                            &original.intent_digest,
                            source,
                        ))
                        .map_err(|error| error.to_string())?
                    )
                );
                observations.push((original.operation_id, false, proof));
            }
            observations
        };
        self.content
            .as_ref()
            .ok_or("original prefix cleanup has no retained content store")?
            .reconcile_producers(&self._writer, &observations)?;
        Ok(())
    }
}
