//! Explicit completed-upload release; Catalog and all other content owners remain independent.

use super::authenticated::AuthenticatedAdmission;
use super::*;

/// Exact original completed upload and stable command; caller cannot supply replacement ownership.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    /// Explicit release-only contract.
    schema: String,
    /// Original immutable administrative command.
    command_id: String,
    /// Canonical complete original upload receipt identity.
    original_upload_digest: String,
    /// Bounded original retention decision.
    reason: String,
}

/// Commits one same-tenant original release without changing completion, Catalog or physical bytes.
pub(super) fn retire(
    catalog: &ArtifactCatalog,
    admission: &AuthenticatedAdmission<'_>,
    request: &Request,
) -> Result<Response, HttpError> {
    let id = request
        .path
        .strip_prefix("/v1/artifact-uploads/")
        .and_then(|path| path.strip_suffix("/retire"))
        .filter(|id| !id.is_empty() && !id.contains('/'))
        .ok_or_else(|| HttpError::bad_request("retirement requires one exact original upload"))?;
    let command: Command = parse_json(&request.body)?;
    if command.schema != "roboguide.artifact-upload-retirement-command/v0.1" {
        return Err(HttpError::bad_request(
            "unsupported upload retirement command",
        ));
    }
    let _writer = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload retirement writer unavailable"))?;
    catalog.ensure_available()?;
    admission
        .revalidate_artifact_principal(admission.principal)
        .map_err(HttpError::forbidden)?;
    let upload = catalog
        .upload_record(id)?
        .ok_or_else(|| HttpError::not_found("original upload is unavailable"))?;
    if upload.tenant_id != admission.principal.tenant_id
        || (admission.principal.grant.role != ArtifactRole::Admin
            && upload.principal_id != admission.principal.subject)
    {
        return Err(HttpError::forbidden(
            "upload retirement requires its same-tenant owner or administrator",
        ));
    }
    if domain::artifact_adoption_digest(&upload).map_err(HttpError::internal)?
        != command.original_upload_digest
    {
        return Err(HttpError::conflict(
            "upload retirement changed its original complete receipt",
        ));
    }
    {
        let receipts = catalog.upload_retirements.lock().map_err(|_| {
            HttpError::service_unavailable("upload retirement evidence unavailable")
        })?;
        if let Some(original) = receipts
            .values()
            .find(|row| row.command_id == command.command_id)
        {
            if original.upload != upload
                || original.reason != command.reason
                || original.principal_id != admission.principal.subject
                || original.tenant_id != admission.principal.tenant_id
            {
                return Err(HttpError::conflict(
                    "original upload retirement command has different input or owner",
                ));
            }
            return Ok(Response::Json(
                "200 OK",
                serde_json::json!({"receipt":original,"replayed":true,"cas_deleted":false}),
            ));
        }
        if receipts.contains_key(id) {
            return Err(HttpError::conflict(
                "original upload ownership is already retired",
            ));
        }
    }
    let store = catalog.retained_store.as_ref().ok_or_else(|| {
        HttpError::conflict("upload retirement requires its actual retained Artifact owner")
    })?;
    if upload.phase != domain::ArtifactUploadPhase::Completed {
        return Err(HttpError::conflict(
            "only original Completed upload ownership can be retired",
        ));
    }
    store
        .verify_artifact(
            upload.content_digest.as_deref().unwrap_or(""),
            upload.byte_size.unwrap_or(u64::MAX),
        )
        .map_err(|error| HttpError::conflict(error.to_string()))?;
    let receipt = domain::ArtifactUploadRetirement {
        schema:"roboguide.artifact-upload-retirement/v0.1".into(),
        command_id: command.command_id,
        store_owner_id: store.owner_id().ok_or_else(|| HttpError::conflict("actual Artifact owner is unavailable"))?.into(),
        upload,
        principal_id:admission.principal.subject.clone(),tenant_id:admission.principal.tenant_id.clone(),
        certificate_digest:admission.principal.certificate_digest.clone(),
        canonical_grant_json:serde_json::to_string(&serde_json::json!({"registry_id":admission.principal.registry_id,"registry_revision":admission.principal.registry_revision,"grant":admission.principal.grant,"certificate_not_after_ms":admission.principal.certificate_expires_at_ms,"legacy_mapped":admission.principal.legacy_mapped})).map_err(|error|HttpError::internal(error.to_string()))?,
        admitted_at_ms:catalog.next_timestamp()?.as_millis(),reason:command.reason,
    };
    receipt.validate().map_err(HttpError::conflict)?;
    catalog.append_authorized_under_writer(
        EventPayload::ArtifactUploadRetired {
            receipt: receipt.clone(),
        },
        admission,
    )?;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"receipt":receipt,"replayed":false,"cas_deleted":false}),
    ))
}

impl ArtifactCatalog {
    /// Reads only the immutable original release; absence never implies content availability.
    pub(super) fn upload_retirement(
        &self,
        id: &str,
    ) -> Result<Option<domain::ArtifactUploadRetirement>, HttpError> {
        Ok(self
            .upload_retirements
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload retirement evidence unavailable"))?
            .get(id)
            .cloned())
    }
}
