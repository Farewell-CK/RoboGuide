//! Read filtering and per-chunk content checks preserve tenant, immutable scope and current identity.

use super::*;

/// Projects exact known typed-map ownership for content policy, refusing missing historical LocalSystem.
pub(crate) fn map_authorization_manifest(
    map: &MapArtifactManifest,
) -> Result<MemoryArtifactManifest, String> {
    domain::map_memory_manifest(map)
}

/// Checks typed resource bounds and its original producer namespace without inventing missing ownership.
pub(super) fn authorized_map_read(
    _catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
    map: &MapArtifactManifest,
    operation: domain::MemoryAccessOperation,
    import_id: Option<&str>,
) -> Result<(), HttpError> {
    if import_id.is_none()
        && principal.grant.role != ArtifactRole::Admin
        && !principal
            .grant
            .map_ids
            .contains(map.selector().map_id().as_str())
    {
        return Err(HttpError::forbidden(
            "typed map is outside the current resource grant",
        ));
    }
    authority
        .authorize_map_access(principal, map, operation, import_id)
        .map_err(HttpError::forbidden)
}

/// Produces a stable filtered discovery view; resource grants never reveal foreign tenant manifests.
pub(super) fn list_authorized_memories(
    catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
) -> Result<Response, HttpError> {
    let mut permitted = std::collections::BTreeSet::new();
    for manifest in catalog.memories()? {
        if (principal.grant.role == ArtifactRole::Admin
            || principal
                .grant
                .memory_ids
                .contains(manifest.selector().memory_id().as_str()))
            && authority
                .authorize_memory_access(
                    principal,
                    &manifest,
                    domain::MemoryAccessOperation::Discover,
                    None,
                )
                .is_ok()
        {
            permitted.insert((
                manifest.selector().memory_id().as_str().to_string(),
                manifest.selector().revision_id().as_str().to_string(),
            ));
        }
    }
    for map in catalog.revisions()? {
        if authorized_map_read(
            catalog,
            authority,
            principal,
            map.manifest(),
            domain::MemoryAccessOperation::Discover,
            None,
        )
        .is_ok()
        {
            permitted.insert((
                map.manifest().selector().map_id().as_str().to_string(),
                map.manifest().selector().revision_id().as_str().to_string(),
            ));
        }
    }
    let mut response = super::server::list_memories(catalog)?;
    let Response::Json(_, value) = &mut response else {
        return Err(HttpError::internal(
            "memory listing returned a non-JSON response",
        ));
    };
    if let Some(items) = value
        .get_mut("memories")
        .and_then(serde_json::Value::as_array_mut)
    {
        items.retain(|item| {
            item.get("selector").is_some_and(|s| {
                s.get("memory_id")
                    .and_then(serde_json::Value::as_str)
                    .zip(s.get("revision_id").and_then(serde_json::Value::as_str))
                    .is_some_and(|(m, r)| permitted.contains(&(m.to_string(), r.to_string())))
            })
        });
    }
    Ok(response)
}

/// Rechecks every protected reference before a blob chunk; a new readable alias cannot bypass scope.
pub(super) fn authorize_blob_read(
    catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
    digest: &str,
    import_id: Option<&str>,
) -> Result<(), HttpError> {
    let _gate = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("content authority ordering unavailable"))?;
    catalog
        .event_log
        .require_writes_enabled()
        .map_err(|_| HttpError::service_unavailable("content authority is write-disabled"))?;
    catalog.event_log.require_read_authority().map_err(|_| {
        HttpError::service_unavailable("current content authority epoch or quorum is unavailable")
    })?;
    authority
        .revalidate_artifact_principal(principal)
        .map_err(HttpError::forbidden)?;
    let mut referenced = false;
    for manifest in catalog.memories()? {
        if manifest
            .artifact()
            .is_some_and(|a| a.content_digest().as_str() == digest)
        {
            referenced = true;
            if manifest.visibility() != domain::MemoryVisibility::Exchangeable {
                return Err(HttpError::forbidden(
                    "discoverable metadata does not offer content exchange",
                ));
            }
            if authority
                .authorize_handoff_blob_reference(principal, &manifest, import_id)
                .map_err(HttpError::forbidden)?
            {
                continue;
            }
            authority
                .authorize_memory_access(
                    principal,
                    &manifest,
                    domain::MemoryAccessOperation::Read,
                    import_id,
                )
                .map_err(HttpError::forbidden)?;
        }
    }
    for map in catalog.revisions()? {
        if map.manifest().artifact().content_digest().as_str() == digest {
            referenced = true;
            authorized_map_read(
                catalog,
                authority,
                principal,
                map.manifest(),
                domain::MemoryAccessOperation::Read,
                import_id,
            )?;
        }
    }
    if !referenced
        && principal.grant.role != ArtifactRole::Admin
        && !principal.grant.artifact_digests.contains(digest)
    {
        return Err(HttpError::forbidden(
            "orphan CAS bytes require an explicit digest grant",
        ));
    }
    Ok(())
}
