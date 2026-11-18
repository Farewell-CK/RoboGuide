//! Content publication releases the application writer and reacquires original authority to commit.

use super::*;

/// Original authenticated operation context, never reconstructed from request-supplied readiness.
pub(super) struct Context {
    /// Current Controller authority for source ownership and final permission revalidation.
    pub controller: Arc<Mutex<ControllerState>>,
    /// Original process-owned event store.
    pub log: state::SqliteEventLog,
    /// Actual authenticated request witness and original FIFO writer.
    pub gate: Arc<AuthorizedRequestGate>,
    /// Explicit deployment-owned destination.
    pub root: std::path::PathBuf,
    /// Original certificate-derived maintenance subject.
    pub subject: String,
    /// Original authorized global maintenance scope.
    pub tenant: String,
}

/// Preserves storage refusal classes when a worker terminates before returning an operation token.
fn worker_error(error: tokio::task::JoinError) -> state::SqliteEventLogError {
    state::SqliteEventLogError::ArchiveSink(format!("content archive worker failed: {error}"))
}

impl Context {
    /// Freezes, publishes and finalizes one original bounded operation using the real Controller clock.
    pub(super) async fn run(
        self,
        command: ContentArchiveCommand,
        clock: &(impl Clock + Sync),
    ) -> Result<state::ArchiveManifest, state::SqliteEventLogError> {
        let Self {
            controller,
            log,
            gate,
            root,
            subject,
            tenant,
        } = self;
        let source_log = log.clone();
        let source_gate = gate.clone();
        let (prepared, sink) = tokio::task::spawn_blocking(move || {
            let _guard = source_gate
                .lock()
                .map_err(state::SqliteEventLogError::ArchivePolicy)?;
            let source = {
                let live = controller
                    .lock()
                    .map_err(|_| state::SqliteEventLogError::LockPoisoned)?;
                live.memory_preparation
                    .catalog
                    .as_ref()
                    .ok_or_else(|| {
                        state::SqliteEventLogError::ArchivePolicy(
                            "Archive requires its original Catalog/source backend".into(),
                        )
                    })?
                    .archive_source()
                    .map_err(state::SqliteEventLogError::ArchivePolicy)?
            };
            let source_owner = source
                .owner_id()
                .ok_or_else(|| {
                    state::SqliteEventLogError::ArchivePolicy(
                        "Archive source lacks its original managed identity".into(),
                    )
                })?
                .to_string();
            let request = state::ArchiveRequest {
                command_id: command.command_id,
                principal: subject,
                tenant,
                source_owner,
                destination: root
                    .to_str()
                    .ok_or_else(|| {
                        state::SqliteEventLogError::ArchivePolicy(
                            "Archive path must be UTF-8".into(),
                        )
                    })?
                    .into(),
                authority_id: source_log.archive_authority_id()?,
                budget_ms: command.budget_ms,
            };
            let current_gate = source_gate.clone();
            let permission = Arc::new(move || {
                let live = controller
                    .lock()
                    .map_err(|_| "Archive authority mutex is poisoned".to_string())?;
                current_gate.validate(&live)
            });
            let sink = application::archive_content::ContentArchiveSink::new(source, permission);
            let prepared = source_log.prepare_content_archive(command.end_sequence, request)?;
            Ok::<_, state::SqliteEventLogError>((prepared, sink))
        })
        .await
        .map_err(worker_error)??;
        let (published, sink) = tokio::task::spawn_blocking(move || {
            let published =
                state::SqliteEventLog::publish_prepared_content_archive(prepared, &sink)?;
            Ok::<_, state::SqliteEventLogError>((published, sink))
        })
        .await
        .map_err(worker_error)??;
        application::run_synchronous_transaction(|| {
            let _guard = gate
                .lock()
                .map_err(state::SqliteEventLogError::ArchivePolicy)?;
            log.finish_content_archive_at(published, &sink, clock.now())
        })
    }
}
