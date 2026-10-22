//! Actual authenticated unpublished Archive cleanup and bounded original-operation observation.

use super::*;

/// One stable actual HTTP outcome, independent of software copy or physical lifecycle strings.
type Outcome = (&'static str, serde_json::Value);
/// Internal admission rejection retaining a concrete transport status and original error.
type Rejection = (&'static str, String);

/// Explicit original cleanup input; caller, tenant, source, destination and clocks are never body fields.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    /// Exact supported cleanup contract.
    schema: String,
    /// First cleanup ID, never the copy command ID.
    command_id: String,
    /// Exact original retained copy command.
    original_command_id: String,
    /// Full original immutable copy intent identity.
    original_intent_digest: String,
    /// Bounded first cleanup budget, separate from the original copy budget.
    budget_ms: u64,
}

/// Preserves exact original rejection classes without disguising uncertain effects as non-execution.
fn state_error(error: state::SqliteEventLogError) -> Rejection {
    let status = if matches!(
        error,
        state::SqliteEventLogError::ArchivePolicy(_) | state::SqliteEventLogError::Codec(_)
    ) {
        "409 Conflict"
    } else {
        "503 Service Unavailable"
    };
    (status, error.to_string())
}

/// Renders only explicit original evidence; a failed response never means its copy or cleanup did not run.
fn render(result: Result<serde_json::Value, Rejection>) -> Outcome {
    match result {
        Ok(value) => ("200 OK", value),
        Err((status, error)) => (
            status,
            serde_json::json!({"error":error,"maintenance_outcome":"not_confirmed"}),
        ),
    }
}

/// Retires only one exact source pin after the actual source/archive writers and durable tombstone agree.
pub(super) fn abort_command(
    body: &[u8],
    subject: String,
    tenant: Option<String>,
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<AuthorizedRequestGate>,
) -> Outcome {
    render((|| {
        if body.len() > 4096 {
            return Err((
                "400 Bad Request",
                "original Archive cleanup command exceeds bound".into(),
            ));
        }
        let command: Command =
            serde_json::from_slice(body).map_err(|error| ("400 Bad Request", error.to_string()))?;
        if command.schema != "roboguide.archive-abort-command/v0.1" {
            return Err((
                "400 Bad Request",
                "unsupported original Archive cleanup contract".into(),
            ));
        }
        let _writer = gate.lock().map_err(|error| ("403 Forbidden", error))?;
        let original = log
            .archive_operation(&command.original_command_id)
            .map_err(state_error)?
            .ok_or_else(|| ("404 Not Found", "original Archive command is absent".into()))?
            .0;
        let root = std::env::var_os("ROBOGUIDE_EVENT_ARCHIVE_DIRECTORY")
            .map(std::path::PathBuf::from)
            .filter(|path| path.is_absolute())
            .ok_or_else(|| {
                (
                    "503 Service Unavailable",
                    "original Archive cleanup requires its explicit deployment directory".into(),
                )
            })?;
        if std::path::Path::new(&original.request.destination) != root
            || original.request.authority_id != log.archive_authority_id().map_err(state_error)?
        {
            return Err(("409 Conflict","original Archive source/authority/destination differs from current declared writer".into()));
        }
        let source = {
            let live = controller.lock().map_err(|_| {
                (
                    "503 Service Unavailable",
                    "Controller authority mutex is poisoned".into(),
                )
            })?;
            live.memory_preparation
                .catalog
                .as_ref()
                .ok_or_else(|| {
                    (
                        "503 Service Unavailable",
                        "original Archive source Catalog is unavailable".into(),
                    )
                })?
                .archive_source()
                .map_err(|error| ("503 Service Unavailable", error))?
        };
        if source.owner_id() != Some(original.request.source_owner.as_str()) {
            return Err((
                "409 Conflict",
                "original Archive source belongs to another CAS owner".into(),
            ));
        }
        let request = state::ArchiveAbortRequest {
            command_id: command.command_id,
            original_command_id: command.original_command_id,
            original_intent_digest: command.original_intent_digest,
            principal: subject,
            tenant: tenant.unwrap_or_else(|| "*".into()),
            budget_ms: command.budget_ms,
        };
        let current_gate = gate.clone();
        let shared = controller.clone();
        let witness = Arc::new(move || {
            let live = shared
                .lock()
                .map_err(|_| "original cleanup Controller mutex is poisoned".to_string())?;
            current_gate.validate(&live)
        });
        let sink = application::archive_content::ContentArchiveSink::new(source, witness);
        let receipt = log.abort_archive(request, &sink).map_err(state_error)?;
        Ok(
            serde_json::json!({"schema":"roboguide.archive-abort-result/v0.1","receipt":receipt,"original_copy_budget_renewed":false,"partial_content_deleted":false,"device_stop_proof":null}),
        )
    })())
}

/// Returns bounded original metadata and hashes under the actual current global administrator witness.
pub(super) fn observe(
    query: &BTreeMap<String, String>,
    log: &state::SqliteEventLog,
    gate: &Arc<AuthorizedRequestGate>,
) -> Outcome {
    render((|| {
        let id = query
            .get("command_id")
            .filter(|value| {
                !value.is_empty() && value.len() <= 128 && !value.chars().any(char::is_control)
            })
            .ok_or_else(|| {
                (
                    "400 Bad Request",
                    "original operation observation requires one bounded command_id".into(),
                )
            })?;
        let _writer = gate.lock().map_err(|error| ("403 Forbidden", error))?;
        log.require_read_authority().map_err(state_error)?;
        match query.get("kind").map_or("copy", String::as_str) {
            "abort" => {
                let (intent, receipt) = log
                    .archive_abort_operation(id)
                    .map_err(state_error)?
                    .ok_or_else(|| {
                        ("404 Not Found", "original cleanup command is absent".into())
                    })?;
                Ok(
                    serde_json::json!({"schema":"roboguide.archive-operation-observation/v0.1","kind":"abort","intent":intent,"receipt":receipt,"observation_only":true}),
                )
            }
            "copy" => {
                let (intent, publication) = log
                    .archive_operation(id)
                    .map_err(state_error)?
                    .ok_or_else(|| ("404 Not Found", "original copy command is absent".into()))?;
                let aborted = log.archive_is_aborted(id).map_err(state_error)?;
                Ok(
                    serde_json::json!({"schema":"roboguide.archive-operation-observation/v0.1","kind":"copy","request":intent.request,"original_intent_digest":intent.digest().map_err(|error|("503 Service Unavailable",error))?,"manifest":intent.manifest,"catalog_digest":intent.catalog_digest,"original_budget":{"boot_id":intent.boot_id,"time_namespace":intent.time_namespace,"issued_ms":intent.issued_ms,"deadline_ms":intent.deadline_ms},"input_count":intent.inputs.len(),"publication_receipt":publication,"original_abort_confirmed":aborted,"observation_only":true}),
                )
            }
            _ => Err((
                "400 Bad Request",
                "unknown original Archive operation kind".into(),
            )),
        }
    })())
}
