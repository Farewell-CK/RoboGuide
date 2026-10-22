//! Actual tenant administrator adoption preserves the original publication and never grants use rights.

use super::authenticated::AuthenticatedAdmission;
use super::*;
use domain::{ArtifactAdoptionAuthority, ArtifactCatalogAdoption, LegacyCatalogPublication};

/// Closed original administrative command; neither manifests nor historical subjects come from JSON.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    /// Explicit current-retention operation, not a publication compatibility alias.
    schema: String,
    /// First idempotent actual administrator operation.
    command_id: String,
    /// Full original immutable stored publication envelope digest from the read-only view.
    original_publication_digest: String,
}

/// Resolves exactly one logical selector without a wildcard, prefix or arbitrary source path.
fn selector(path: &str, suffix: &str) -> Result<MemorySelector, HttpError> {
    let value = path
        .strip_prefix("/v1/artifact-references/")
        .and_then(|value| value.strip_suffix(suffix))
        .ok_or_else(|| HttpError::bad_request("invalid original Catalog adoption route"))?;
    let parts = value.split('/').collect::<Vec<_>>();
    if parts.len() != 2 {
        return Err(HttpError::bad_request(
            "adoption requires an exact selector",
        ));
    }
    Ok(MemorySelector::new(
        MemoryId::new(parts[0]).map_err(|error| HttpError::bad_request(error.to_string()))?,
        MemoryRevisionId::new(parts[1])
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    ))
}

/// Extracts the actual original envelope without rewriting payload strings or creating a new manifest.
fn original(
    catalog: &ArtifactCatalog,
    selector: &MemorySelector,
) -> Result<LegacyCatalogPublication, HttpError> {
    let mut selected = None;
    let mut bytes = 0usize;
    for event in catalog
        .event_log
        .catalog_replay_stored_events_bounded(64 * 1024 * 1024, 16_384)
        .map_err(|error| HttpError::service_unavailable(error.to_string()))?
    {
        bytes = bytes
            .checked_add(event.payload_json.len())
            .ok_or_else(|| HttpError::conflict("legacy Catalog evidence scan overflow"))?;
        if bytes > 64 * 1024 * 1024 {
            return Err(HttpError::conflict(
                "legacy Catalog evidence exceeds its original bounded scan",
            ));
        }
        let payload: EventPayload = serde_json::from_str(&event.payload_json)
            .map_err(|error| HttpError::conflict(error.to_string()))?;
        let matches = match &payload {
            EventPayload::MemoryManifestPublished { manifest } => manifest.selector() == selector,
            EventPayload::MapArtifactPublished { manifest } => {
                domain::map_memory_manifest(manifest)
                    .map_err(HttpError::conflict)?
                    .selector()
                    == selector
            }
            _ => false,
        };
        if matches && selected.is_none() {
            selected = Some(LegacyCatalogPublication {
                sequence: event.sequence,
                event_id: event.event_id,
                timestamp_ms: event.timestamp_ms,
                correlation_id: event.correlation_id,
                causation_id: event.causation_id,
                payload_schema: event.payload_schema,
                payload_json: event.payload_json,
            });
        }
    }
    selected
        .ok_or_else(|| HttpError::not_found("original legacy Catalog publication is unavailable"))
}

/// Revalidates the actual current tenant administrator; original missing attribution remains unknown.
fn require_admin(admission: &AuthenticatedAdmission<'_>) -> Result<(), HttpError> {
    admission
        .revalidate_artifact_principal(admission.principal)
        .map_err(HttpError::forbidden)?;
    if admission.principal.grant.role != ArtifactRole::Admin {
        return Err(HttpError::forbidden(
            "legacy Catalog adoption requires its current tenant administrator",
        ));
    }
    Ok(())
}

/// Exposes only exact original publication identity and the already committed current adoption receipt.
pub(super) fn observe(
    catalog: &ArtifactCatalog,
    admission: &AuthenticatedAdmission<'_>,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = selector(path, "/legacy")?;
    let _writer = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("Catalog writer unavailable"))?;
    require_admin(admission)?;
    catalog.ensure_available()?;
    catalog
        .event_log
        .require_read_authority()
        .map_err(|error| HttpError::service_unavailable(error.to_string()))?;
    let publication = original(catalog, &selector)?;
    let (manifest, _) = publication.manifest().map_err(HttpError::conflict)?;
    admission
        .authority
        .authorize_memory_access(
            admission.principal,
            &manifest,
            domain::MemoryAccessOperation::Discover,
            None,
        )
        .map_err(HttpError::forbidden)?;
    let retention = catalog
        .retention
        .lock()
        .map_err(|_| HttpError::service_unavailable("Catalog retention unavailable"))?;
    if retention
        .record(&selector)
        .is_some_and(|record| record.tenant_id != admission.principal.tenant_id)
    {
        return Err(HttpError::forbidden(
            "current Catalog reference belongs to another tenant",
        ));
    }
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"schema":"roboguide.legacy-catalog-observation/v0.1", "original_publication_digest":domain::artifact_adoption_digest(&publication).map_err(HttpError::internal)?, "publication":publication, "current_adoption":retention.adoption(&selector), "has_current_reference":retention.record(&selector).is_some(), "historical_publisher":null,"historical_tenant":null,"observation_only":true}),
    ))
}

/// Verifies real same-owner bytes before atomically committing current retention and full original receipt.
pub(super) fn adopt(
    catalog: &ArtifactCatalog,
    admission: &AuthenticatedAdmission<'_>,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = selector(path, "/adopt")?;
    let command: Command = parse_json(&request.body)?;
    if command.schema != "roboguide.legacy-catalog-adoption-command/v0.1" {
        return Err(HttpError::bad_request(
            "unsupported original Catalog adoption command",
        ));
    }
    let _writer = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("Catalog writer unavailable"))?;
    require_admin(admission)?;
    catalog.ensure_available()?;
    catalog
        .event_log
        .require_writes_enabled()
        .map_err(|error| HttpError::service_unavailable(error.to_string()))?;
    let publication = original(catalog, &selector)?;
    let (original_manifest, _) = publication.manifest().map_err(HttpError::conflict)?;
    admission
        .authority
        .authorize_memory_access(
            admission.principal,
            &original_manifest,
            domain::MemoryAccessOperation::Discover,
            None,
        )
        .map_err(HttpError::forbidden)?;
    if domain::artifact_adoption_digest(&publication).map_err(HttpError::internal)?
        != command.original_publication_digest
    {
        return Err(HttpError::conflict(
            "legacy adoption changed original publication identity",
        ));
    }
    {
        let retained = catalog
            .retention
            .lock()
            .map_err(|_| HttpError::service_unavailable("Catalog retention unavailable"))?;
        if let Some(previous) = retained.adoption_command(&command.command_id) {
            if previous.record.selector != selector
                || previous.authority.principal_id != admission.principal.subject
                || previous.authority.tenant_id != admission.principal.tenant_id
                || previous.publication != publication
            {
                return Err(HttpError::conflict(
                    "original adoption command has different input/owner",
                ));
            }
            return Ok(Response::Json(
                "200 OK",
                serde_json::json!({"receipt":previous,"replayed":true,"new_read_or_use_permission":false}),
            ));
        }
        if retained.record(&selector).is_some() {
            return Err(HttpError::conflict(
                "Catalog already has original retention; adoption cannot replace it",
            ));
        }
    }
    let (manifest, typed) = publication.manifest().map_err(HttpError::conflict)?;
    let mut parents = Vec::new();
    if let Some(map) = &typed
        && let Some(parent) = map.parent_revision_id()
    {
        let source = catalog
            .revision(&MapRevisionSelector::new(
                map.selector().map_id().clone(),
                parent.clone(),
            ))?
            .ok_or_else(|| HttpError::conflict("legacy typed parent publication is unavailable"))?;
        parents.push(domain::map_memory_manifest(source.manifest()).map_err(HttpError::conflict)?);
    }
    let authority = ArtifactAdoptionAuthority {
        principal_id: admission.principal.subject.clone(), tenant_id: admission.principal.tenant_id.clone(), certificate_digest: admission.principal.certificate_digest.clone(),
        canonical_grant_json: serde_json::to_string(&serde_json::json!({"registry_id":admission.principal.registry_id,"registry_revision":admission.principal.registry_revision,"grant":admission.principal.grant,"certificate_not_after_ms":admission.principal.certificate_expires_at_ms,"legacy_mapped":admission.principal.legacy_mapped})).map_err(|error| HttpError::internal(error.to_string()))?,
    };
    let source = admission
        .publication_source()
        .map_err(HttpError::forbidden)?;
    let now = catalog.next_timestamp()?;
    let events = catalog
        .publication_reference_events_inner(&manifest, &parents, typed.as_ref(), &source, now, true)
        .map_err(HttpError::conflict)?;
    let Some(EventPayload::ArtifactCatalogReferenceTransition { mut record }) =
        events.into_iter().next()
    else {
        return Err(HttpError::conflict(
            "legacy adoption requires an actual retained CAS owner",
        ));
    };
    record.authority_digest =
        domain::artifact_adoption_digest(&(&command.command_id, &publication, &authority))
            .map_err(HttpError::internal)?;
    let receipt = ArtifactCatalogAdoption {
        schema: "roboguide.artifact-catalog-adoption/v0.1".into(),
        command_id: command.command_id,
        publication,
        authority,
        historical_publisher: None,
        historical_tenant: None,
        record,
    };
    receipt.validate().map_err(HttpError::conflict)?;
    require_admin(admission)?;
    catalog.append_authorized_under_writer(
        EventPayload::ArtifactCatalogLegacyAdopted {
            receipt: receipt.clone(),
        },
        admission,
    )?;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"receipt":receipt,"replayed":false,"new_read_or_use_permission":false}),
    ))
}
