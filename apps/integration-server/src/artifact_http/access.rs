//! Read filtering and per-chunk content checks preserve tenant, immutable scope and current identity.

use super::*;

/// Projects exact known typed-map ownership for content policy, refusing missing historical LocalSystem.
pub(crate) fn map_authorization_manifest(
    map: &MapArtifactManifest,
) -> Result<MemoryArtifactManifest, String> {
    let local_system_id = map
        .producer_local_system_id()
        .ok_or("typed-map original LocalSystem is unavailable")?;
    MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new(map.selector().map_id().as_str()).map_err(|e| e.to_string())?,
            MemoryRevisionId::new(map.selector().revision_id().as_str())
                .map_err(|e| e.to_string())?,
        ),
        domain::MemoryKind::Spatial,
        "typed-map-catalog",
        MemoryOwner::Node {
            node_id: map.producer_node_id().clone(),
            local_system_id: local_system_id.clone(),
        },
        domain::MemoryScope::Global,
        domain::MemoryVisibility::Exchangeable,
        domain::SPATIAL_MEMORY_SCHEMA_V0_1,
        map.media_type(),
        Some(domain::MemoryArtifactRef::new(
            map.artifact().content_digest().clone(),
            map.artifact().byte_size(),
        )),
        Some(map.source_mission_id().clone()),
        map.source_execution_id().map(str::to_string),
        map.source_task_ref().cloned(),
        map.created_at(),
    )
    .map_err(|e| e.to_string())
}

/// Checks typed resource bounds and its original producer namespace without inventing missing ownership.
pub(super) fn authorized_map_read(
    _catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
    map: &MapArtifactManifest,
    operation: domain::MemoryAccessOperation,
) -> Result<(), HttpError> {
    if principal.grant.role != ArtifactRole::Admin
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
        .authorize_map_access(principal, map, operation)
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
    let Response::Json(_, value) = &mut response;
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
