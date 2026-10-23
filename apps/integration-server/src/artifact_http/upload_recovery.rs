//! One-shot explicit Prepared recovery creates an independently owned normal upload.

use super::authenticated::AuthenticatedAdmission;
use super::server::{restore_after_append, take_for_append};
use super::*;
use domain::{ArtifactUploadPhase, ArtifactUploadRecord, ArtifactUploadRecovery};
use std::io::Read;

/// Recognizes only the exact bounded metadata-observation route; no payload route uses its exception.
pub(super) fn observation_keys(path: &str) -> Option<(&str, &str)> {
    direct_observation_keys(path).or_else(|| {
        path.strip_suffix("/assessment")
            .and_then(direct_observation_keys)
    })
}

/// Parses only the original exact route; literal command ID assessment remains a normal observation.
fn direct_observation_keys(path: &str) -> Option<(&str, &str)> {
    let (upload, command) = path
        .strip_prefix("/v1/artifact-uploads/")?
        .split_once("/recoveries/")?;
    let bounded = |s: &str| {
        !s.is_empty()
            && s.len() <= 128
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
    };
    (bounded(upload) && bounded(command)).then_some((upload, command))
}

impl ArtifactCatalog {
    /// Reads an original uploader's committed local record without acquiring writable-upload permission.
    pub(super) fn observe_owned_upload(
        &self,
        id: &str,
        principal: &ArtifactPrincipal,
    ) -> Result<ArtifactUploadRecord, HttpError> {
        self.event_log
            .require_read_authority()
            .map_err(|e| HttpError::service_unavailable(e.to_string()))?;
        let record = self
            .upload_records
            .lock()
            .map_err(|_| HttpError::service_unavailable("committed upload cache unavailable"))?
            .get(id)
            .cloned()
            .ok_or_else(|| HttpError::not_found("original upload unavailable"))?;
        if record.principal_id != principal.subject || record.tenant_id != principal.tenant_id {
            return Err(HttpError::forbidden(
                "upload observation crosses original principal/tenant",
            ));
        }
        Ok(record)
    }
}

/// Observes one exact original result and the local failure generation; this never opens content.
pub(super) fn observe(
    catalog: &ArtifactCatalog,
    admission: &AuthenticatedAdmission<'_>,
    path: &str,
    audit_persisted: bool,
) -> Result<Response, HttpError> {
    let (upload, command) = observation_keys(path)
        .ok_or_else(|| HttpError::not_found("unknown recovery observation"))?;
    let _writer = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("original observation writer unavailable"))?;
    admission
        .revalidate_artifact_principal(admission.principal)
        .map_err(HttpError::forbidden)?;
    let status = catalog
        .event_log
        .write_status()
        .map_err(|e| HttpError::service_unavailable(e.to_string()))?;
    if status
        .failure
        .as_ref()
        .is_some_and(|failure| failure.restart_required)
        || catalog
            .recovery_fence
            .lock()
            .map_err(|_| HttpError::service_unavailable("original catalog fence unavailable"))?
            .is_some()
    {
        let original = status.checkpoint_identity.as_deref().ok_or_else(|| {
            HttpError::service_unavailable("original committed checkpoint unavailable")
        })?;
        catalog
            .replica_content
            .as_ref()
            .ok_or_else(|| {
                HttpError::service_unavailable(
                    "original replica/catalog uncertainty requires startup replay",
                )
            })?
            .verify_committed_checkpoint(original)
            .map_err(HttpError::service_unavailable)?;
    }
    let source = catalog.observe_owned_upload(upload, admission.principal)?;
    let record = catalog
        .upload_recoveries
        .lock()
        .map_err(|_| HttpError::service_unavailable("committed recovery cache unavailable"))?
        .get(command)
        .filter(|r| r.upload.upload_id == upload)
        .cloned()
        .ok_or_else(|| HttpError::not_found("original recovery unavailable"))?;
    if path
        .strip_suffix("/assessment")
        .and_then(direct_observation_keys)
        .is_some()
    {
        record.upload.validates_successor(&source).map_err(|e| {
            HttpError::service_unavailable(format!("original source assessment differs: {e}"))
        })?;
        let replacement = catalog
            .upload_records
            .lock()
            .map_err(|_| HttpError::service_unavailable("committed replacement cache unavailable"))?
            .get(&record.replacement_admission.upload_id)
            .cloned();
        if let Some(current) = &replacement {
            current.validate().map_err(HttpError::service_unavailable)?;
            let mut original = current.clone();
            original.phase = ArtifactUploadPhase::Started;
            original.content_digest = None;
            original.byte_size = None;
            original.prepared_at_ms = None;
            original.completed_at_ms = None;
            if original != record.replacement_admission {
                return Err(HttpError::service_unavailable(
                    "replacement assessment changed its original admission",
                ));
            }
        }
        let (source_retirement, replacement_retirement) = {
            let retirements = catalog.upload_retirements.lock().map_err(|_| {
                HttpError::service_unavailable("committed retirement cache unavailable")
            })?;
            (
                retirements.get(&source.upload_id).cloned(),
                retirements
                    .get(&record.replacement_admission.upload_id)
                    .cloned(),
            )
        };
        for (receipt, current) in [
            (source_retirement.as_ref(), Some(&source)),
            (replacement_retirement.as_ref(), replacement.as_ref()),
        ] {
            if let Some(receipt) = receipt {
                receipt.validate().map_err(HttpError::service_unavailable)?;
                if Some(&receipt.upload) != current {
                    return Err(HttpError::service_unavailable(
                        "retirement assessment differs from its exact current upload",
                    ));
                }
            }
        }
        return Ok(Response::Json(
            "200 OK",
            serde_json::json!({
                "schema":"roboguide.artifact-upload-recovery-assessment/v0.1",
                "status":if record.completion.is_some(){"completed"}else{"unknown"},
                "source_record":source,
                "replacement_record_evidence":if replacement.is_some(){"present"}else{"unavailable"},
                "replacement_record":replacement,
                "source_retirement":source_retirement,"replacement_retirement":replacement_retirement,
                "record":record,"repeat_allowed":false,"committed_local":true,
                "audit_persisted":audit_persisted,"write_status":status,
            }),
        ));
    }
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"schema":"roboguide.artifact-upload-recovery-observation/v0.1",
        "status":if record.completion.is_some(){"completed"}else{"unknown"},"record":record,
        "committed_local":true,"audit_persisted":audit_persisted,"write_status":status}),
    ))
}

/// Exact original receipt and independent bounded command; caller cannot select source/destination.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    /// Closed explicit recovery semantics.
    schema: String,
    /// One-shot identity; duplicate outcomes remain observations.
    command_id: String,
    /// Canonical complete original Prepared record digest.
    original_upload_digest: String,
    /// Complete original kernel duration, including admission and every subsequent Commit.
    budget_ms: u64,
}

/// Returns original evidence without inferring availability or repeating any byte operation.
fn response(record: &ArtifactUploadRecovery, replayed: bool) -> Response {
    Response::Json(
        "200 OK",
        serde_json::json!({"record":record,"replayed":replayed,
        "original_upload_changed":false,"status":if record.completion.is_some(){"completed"}else{"unknown"}}),
    )
}

/// Admits a durable immutable intent, copies only original permitted bytes and records a normal upload.
pub(super) fn recover(
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    admission: &AuthenticatedAdmission<'_>,
    request: &Request,
) -> Result<Response, HttpError> {
    let id = request
        .path
        .strip_prefix("/v1/artifact-uploads/")
        .and_then(|p| p.strip_suffix("/recover"))
        .filter(|id| !id.is_empty() && !id.contains('/'))
        .ok_or_else(|| HttpError::bad_request("recovery requires one exact original upload"))?;
    let command: Command = parse_json(&request.body)?;
    if command.schema != "roboguide.artifact-upload-recovery-command/v0.1"
        || !(100..=300000).contains(&command.budget_ms)
    {
        return Err(HttpError::bad_request("unsupported recovery schema/budget"));
    }
    let client = catalog.replica_content.as_ref().ok_or_else(|| {
        HttpError::conflict(
            "Prepared recovery requires explicit original owned-child content support",
        )
    })?;
    let record = {
        let _writer = catalog
            .write_gate
            .lock()
            .map_err(|_| HttpError::service_unavailable("recovery writer unavailable"))?;
        catalog.ensure_available()?;
        admission
            .revalidate_artifact_principal(admission.principal)
            .map_err(HttpError::forbidden)?;
        let upload = catalog.owned_upload(id, admission.principal)?;
        if upload.principal_id != admission.principal.subject
            || upload.tenant_id != admission.principal.tenant_id
        {
            return Err(HttpError::forbidden(
                "recovery requires its original stable uploader and tenant",
            ));
        }
        if domain::artifact_adoption_digest(&upload).map_err(HttpError::internal)?
            != command.original_upload_digest
        {
            return Err(HttpError::conflict(
                "recovery changed its exact original upload",
            ));
        }
        if let Some(original) = catalog
            .upload_recoveries
            .lock()
            .map_err(|_| HttpError::service_unavailable("recovery evidence unavailable"))?
            .get(&command.command_id)
        {
            if original.upload != upload || original.budget_ms != command.budget_ms {
                return Err(HttpError::conflict(
                    "recovery command changed its original input/budget",
                ));
            }
            return Ok(response(original, true));
        }
        if upload.phase != ArtifactUploadPhase::Prepared || catalog.upload_retirement(id)?.is_some()
        {
            return Err(HttpError::conflict(
                "recovery requires its unchanged non-retired Prepared upload",
            ));
        }
        let admitted_at_ms = super::upload_authority::now_ms()?;
        let replacement_digest = domain::artifact_adoption_digest(&(id, &command.command_id))
            .map_err(HttpError::internal)?;
        let replacement_id = format!("recovery-{}", &replacement_digest[7..]);
        let replacement = ArtifactUploadRecord {
            upload_id: replacement_id,
            principal_id: upload.principal_id.clone(),
            tenant_id: upload.tenant_id.clone(),
            original_certificate: admission
                .principal
                .certificate_digest
                .clone()
                .unwrap_or_else(|| admission.principal.subject.clone()),
            admitted_at_ms,
            deadline_ms: admitted_at_ms
                .checked_add(command.budget_ms)
                .ok_or_else(|| HttpError::bad_request("recovery deadline overflow"))?,
            phase: ArtifactUploadPhase::Started,
            content_digest: None,
            byte_size: None,
            prepared_at_ms: None,
            completed_at_ms: None,
        };
        let mut record = ArtifactUploadRecovery {schema:"roboguide.artifact-upload-recovery/v0.1".into(),
            command_id:command.command_id,budget_ms:command.budget_ms,reader_id:String::new(),upload,source_owner_id:String::new(),
            replacement_admission:replacement,destination_owner_id:String::new(),destination_device:0,destination_inode:0,
            controller_process_json:String::new(),kernel_budget_json:String::new(),
            canonical_grant_json:serde_json::to_string(&serde_json::json!({"principal":admission.principal.subject,
                "tenant":admission.principal.tenant_id,"certificate":admission.principal.certificate_digest,
                "registry":admission.principal.registry_id,"revision":admission.principal.registry_revision,
                "grant":admission.principal.grant})).map_err(|e|HttpError::internal(e.to_string()))?,completion:None};
        record = client
            .prepare_upload_recovery(record, command.budget_ms)
            .map_err(HttpError::conflict)?;
        catalog.append_authorized_under_writer(
            EventPayload::ArtifactUploadRecoveryTransition {
                record: Box::new(record.clone()),
            },
            admission,
        )?;
        record
    };
    complete(catalog, uploads, admission, client, record)
}

/// Performs only the new intent's first copy; any interruption leaves immutable Unknown for replay.
fn complete(
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    admission: &AuthenticatedAdmission<'_>,
    client: &controller_replication::ControllerContentClient,
    mut record: ArtifactUploadRecovery,
) -> Result<Response, HttpError> {
    let store = catalog
        .retained_store
        .as_ref()
        .ok_or_else(|| HttpError::conflict("actual destination CAS unavailable"))?;
    let id = record.replacement_admission.upload_id.clone();
    check(catalog, admission, client, &record)?;
    catalog.append_authorized(
        EventPayload::ArtifactUploadTransition {
            record: record.replacement_admission.clone(),
        },
        admission,
    )?;
    {
        let _writer = catalog
            .write_gate
            .lock()
            .map_err(|_| HttpError::service_unavailable("recovery writer unavailable"))?;
        check(catalog, admission, client, &record)?;
        let mut registry = uploads
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload quota unavailable"))?;
        if registry.active_uploads() >= registry.max_uploads {
            return Err(HttpError::resource_exhausted(
                "recovery upload count exceeds original limit",
            ));
        }
        let upload = store.begin_upload(id.clone()).map_err(map_cas_error)?;
        registry.insert_new(id.clone(), upload, Instant::now())?;
    }
    let size = record
        .upload
        .byte_size
        .ok_or_else(|| HttpError::conflict("original size absent"))?;
    let mut in_flight = take_for_append(uploads, &id, size)?;
    let copy = (|| {
        let mut reader = client
            .open_upload_recovery(&record)
            .map_err(HttpError::conflict)?;
        let mut buffer = vec![0; 64 * 1024];
        loop {
            check(catalog, admission, client, &record)?;
            let count = reader
                .read(&mut buffer)
                .map_err(|e| HttpError::service_unavailable(e.to_string()))?;
            if count == 0 {
                break;
            }
            let _writer = catalog
                .write_gate
                .lock()
                .map_err(|_| HttpError::service_unavailable("recovery writer unavailable"))?;
            check(catalog, admission, client, &record)?;
            in_flight
                .session
                .upload
                .write_chunk(&buffer[..count])
                .map_err(map_cas_error)?;
            client
                .check_upload_recovery(&record)
                .map_err(HttpError::conflict)?;
        }
        reader
            .completed_recovery_reader()
            .map_err(HttpError::conflict)
    })();
    restore_after_append(uploads, &id, in_flight)?;
    let (reader_receipt_json, reader_authority_digest) = copy?;
    check(catalog, admission, client, &record)?;
    let finalization = Request {
        path: format!("/v1/artifact-uploads/{id}/finalize"),
        body: serde_json::to_vec(
            &serde_json::json!({"content_digest":record.upload.content_digest,"byte_size":size}),
        )
        .map_err(|e| HttpError::internal(e.to_string()))?,
        memory_publisher: None,
    };
    super::upload_authority::finalize_authorized_upload(
        &finalization,
        store,
        uploads,
        catalog,
        admission,
        admission.principal,
    )?;
    let replacement = catalog.owned_upload(&id, admission.principal)?;
    {
        let _writer = catalog
            .write_gate
            .lock()
            .map_err(|_| HttpError::service_unavailable("recovery writer unavailable"))?;
        check(catalog, admission, client, &record)?;
        store
            .verify_artifact(record.upload.content_digest.as_deref().unwrap_or(""), size)
            .map_err(map_cas_error)?;
        record.completion = Some(domain::ArtifactUploadRecoveryCompletion {
            replacement,
            reader_receipt_json,
            reader_authority_digest,
        });
        catalog.append_authorized_under_writer(
            EventPayload::ArtifactUploadRecoveryTransition {
                record: Box::new(record.clone()),
            },
            admission,
        )?;
    }
    Ok(response(&record, false))
}

/// Rechecks current real request authority, original immutable intent, writer and original effect clock.
fn check(
    catalog: &ArtifactCatalog,
    admission: &AuthenticatedAdmission<'_>,
    client: &controller_replication::ControllerContentClient,
    record: &ArtifactUploadRecovery,
) -> Result<(), HttpError> {
    catalog.ensure_available()?;
    admission
        .revalidate_artifact_principal(admission.principal)
        .map_err(HttpError::forbidden)?;
    client
        .check_upload_recovery(record)
        .map_err(HttpError::conflict)?;
    if catalog.owned_upload(&record.upload.upload_id, admission.principal)? != record.upload
        || catalog
            .upload_recoveries
            .lock()
            .map_err(|_| HttpError::service_unavailable("recovery evidence unavailable"))?
            .get(&record.command_id)
            != Some(record)
    {
        return Err(HttpError::conflict(
            "original recovery intent or Prepared source changed",
        ));
    }
    Ok(())
}
