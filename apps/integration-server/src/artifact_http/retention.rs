//! Real publication-owned CAS retention; references grant no content or Group access.

use super::authenticated::AuthenticatedAdmission;
use super::*;
use domain::{ArtifactCatalogReference, ArtifactContentDependency, ArtifactDependencyKind};

/// Original publisher identity supplied by authenticated transport or a frozen application purpose.
#[derive(Debug, Clone)]
pub struct ArtifactPublicationSource {
    /// Actual immutable publication tenant.
    pub(crate) tenant_id: String,
    /// Original stable publisher or delegated administrative principal.
    pub(crate) principal_id: String,
    /// Digest of the complete original transport/delegation authority, never a requested header.
    pub(crate) authority_digest: String,
}

/// Closed explicit retirement request; source tenant and proof are never accepted from the body.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct RetireRequest {
    /// Exact original administrative operation retained for nonrenewing retries.
    command_id: String,
    /// Canonical digest of the complete original acquisition record with no retirement.
    original_digest: String,
}

/// Parses exactly one public logical revision without broadening a prefix or wildcard.
fn selector(path: &str, retire: bool) -> Result<MemorySelector, HttpError> {
    let suffix = path
        .strip_prefix("/v1/artifact-references/")
        .ok_or_else(|| HttpError::bad_request("invalid Artifact reference route"))?;
    let suffix = if retire {
        suffix
            .strip_suffix("/retire")
            .ok_or_else(|| HttpError::bad_request("invalid Artifact retirement route"))?
    } else {
        suffix
    };
    let parts: Vec<_> = suffix.split('/').collect();
    if parts.len() != 2 {
        return Err(HttpError::bad_request(
            "Artifact reference requires one exact Memory revision",
        ));
    }
    Ok(MemorySelector::new(
        MemoryId::new(parts[0]).map_err(|error| HttpError::bad_request(error.to_string()))?,
        MemoryRevisionId::new(parts[1])
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    ))
}

/// Reads original committed ownership using actual current administrator identity and quorum.
pub(super) fn observe(
    catalog: &ArtifactCatalog,
    authority: &AuthenticatedAdmission<'_>,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = selector(path, false)?;
    let _gate = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("Artifact writer unavailable"))?;
    catalog.ensure_available()?;
    authority
        .revalidate_artifact_principal(authority.principal)
        .map_err(HttpError::forbidden)?;
    catalog
        .event_log
        .require_read_authority()
        .map_err(|error| HttpError::service_unavailable(error.to_string()))?;
    let retained = catalog
        .retention
        .lock()
        .map_err(|_| HttpError::service_unavailable("Artifact retention unavailable"))?;
    let record = retained
        .record(&selector)
        .ok_or_else(|| HttpError::not_found("original Catalog reference is unavailable"))?;
    if record.tenant_id != authority.principal.tenant_id {
        return Err(HttpError::forbidden(
            "Artifact reference belongs to another tenant",
        ));
    }
    let mut original = record.clone();
    original.retirement = None;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({
            "record": record,
            "original_digest": evidence_digest(&original).map_err(HttpError::internal)?,
            "record_digest": evidence_digest(record).map_err(HttpError::internal)?,
        }),
    ))
}

/// Retires only an unchanged original publication after current original application-use proof.
pub(super) fn retire(
    catalog: &ArtifactCatalog,
    authority: &AuthenticatedAdmission<'_>,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = selector(path, true)?;
    let input: RetireRequest = parse_json(&request.body)?;
    let _gate = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("Artifact writer unavailable"))?;
    catalog.ensure_available()?;
    catalog
        .event_log
        .require_writes_enabled()
        .map_err(|error| HttpError::service_unavailable(error.to_string()))?;
    let source = authority
        .publication_source()
        .map_err(HttpError::forbidden)?;
    let mut record = catalog
        .retention
        .lock()
        .map_err(|_| HttpError::service_unavailable("Artifact retention unavailable"))?
        .record(&selector)
        .cloned()
        .ok_or_else(|| HttpError::conflict("Catalog has no original accounted ownership"))?;
    if source.tenant_id != record.tenant_id || authority.principal.grant.role != ArtifactRole::Admin
    {
        return Err(HttpError::forbidden(
            "Catalog retirement requires its actual tenant administrator",
        ));
    }
    let mut original = record.clone();
    original.retirement = None;
    if evidence_digest(&original).map_err(HttpError::internal)? != input.original_digest {
        return Err(HttpError::conflict(
            "Catalog retirement original record digest conflicts",
        ));
    }
    let replayed = if let Some(receipt) = &record.retirement {
        if receipt.command_id != input.command_id || receipt.principal_id != source.principal_id {
            return Err(HttpError::conflict(
                "Catalog retirement differs from its original command",
            ));
        }
        true
    } else {
        let proof_digest = authority
            .catalog_retirement_proof(&record)
            .map_err(HttpError::conflict)?;
        record.retirement = Some(domain::ArtifactCatalogRetirement {
            command_id: input.command_id,
            principal_id: source.principal_id,
            proof_digest,
            retired_at_ms: catalog.next_timestamp()?.as_millis(),
        });
        record.validate().map_err(HttpError::bad_request)?;
        catalog.append_authorized_under_writer(
            EventPayload::ArtifactCatalogReferenceTransition {
                record: record.clone(),
            },
            authority,
        )?;
        false
    };
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"record": record, "replayed": replayed}),
    ))
}

/// Hashes canonical original evidence without depending on HTTP JSON whitespace.
pub(crate) fn evidence_digest(value: &impl serde::Serialize) -> Result<String, String> {
    let canonical = serde_json::to_value(value).map_err(|error| error.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&canonical).map_err(|error| error.to_string())?)
    ))
}

/// Pins actual original bytes while retaining semantic provider and complete manifest identity.
fn dependency(
    store: &FileSystemArtifactStore,
    manifest: &MemoryArtifactManifest,
    kind: ArtifactDependencyKind,
    typed_map: Option<&MapArtifactManifest>,
) -> Result<Option<ArtifactContentDependency>, String> {
    let Some(artifact) = manifest.artifact() else {
        return Ok(None);
    };
    let owner = store
        .owner_id()
        .ok_or("Artifact retained owner unavailable")?;
    if let Some(map) = typed_map
        && domain::map_memory_manifest(map)? != *manifest
    {
        return Err("Artifact typed source differs from its complete original map".into());
    }
    store
        .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size())
        .map_err(|error| format!("original Artifact reference bytes unavailable: {error}"))?;
    Ok(Some(ArtifactContentDependency {
        kind,
        store_owner_id: owner.into(),
        provider_id: manifest.provider_id().into(),
        selector: manifest.selector().clone(),
        manifest_digest: domain::memory_manifest_digest(manifest)?,
        typed_map: typed_map
            .map(
                |map| -> Result<domain::ArtifactTypedMapProvenance, String> {
                    Ok(domain::ArtifactTypedMapProvenance {
                        canonical_json: serde_json::to_string(
                            &serde_json::to_value(map).map_err(|error| error.to_string())?,
                        )
                        .map_err(|error| error.to_string())?,
                        manifest_digest: domain::map_manifest_digest(map)?,
                    })
                },
            )
            .transpose()?,
        content_digest: artifact.content_digest().as_str().into(),
        byte_size: artifact.byte_size(),
    }))
}

impl ArtifactCatalog {
    /// Constructs original acquisition events under the caller's actual shared application writer.
    /// No cache mutates before SQL Commit; retries retain the original record and time.
    pub(crate) fn publication_reference_events(
        &self,
        manifest: &MemoryArtifactManifest,
        parents: &[MemoryArtifactManifest],
        typed_map: Option<&MapArtifactManifest>,
        source: &ArtifactPublicationSource,
        now: TimestampMs,
    ) -> Result<Vec<EventPayload>, String> {
        let Some(store) = &self.retained_store else {
            return Ok(Vec::new());
        };
        if parents.len() > 64 {
            return Err("Artifact publication lineage exceeds original dependency bound".into());
        }
        let mut dependencies = Vec::new();
        if let Some(original) =
            dependency(store, manifest, ArtifactDependencyKind::Catalog, typed_map)?
        {
            dependencies.push(original);
        }
        let retained = self
            .retention
            .lock()
            .map_err(|_| "Artifact retention projection unavailable")?;
        for parent in parents {
            let reference = retained
                .record(parent.selector())
                .ok_or("original parent Catalog ownership is unaccounted")?;
            if reference.retirement.is_some() || reference.tenant_id != source.tenant_id {
                return Err(
                    "original parent Catalog is retired or belongs to another tenant".into(),
                );
            }
            let parent_selector = MapRevisionSelector::new(
                domain::MapId::new(parent.selector().memory_id().as_str())
                    .map_err(|error| error.to_string())?,
                domain::MapRevisionId::new(parent.selector().revision_id().as_str())
                    .map_err(|error| error.to_string())?,
            );
            let parent_map = self
                .projection
                .lock()
                .map_err(|_| "Spatial catalog unavailable")?
                .revision(&parent_selector)
                .map(|revision| revision.manifest().clone());
            if let Some(original) = dependency(
                store,
                parent,
                ArtifactDependencyKind::Lineage,
                parent_map.as_ref(),
            )? {
                if !reference.dependencies.iter().any(|entry| {
                    entry.kind == ArtifactDependencyKind::Catalog
                        && entry.selector == original.selector
                        && entry.store_owner_id == original.store_owner_id
                        && entry.provider_id == original.provider_id
                        && entry.manifest_digest == original.manifest_digest
                        && entry.typed_map == original.typed_map
                        && entry.content_digest == original.content_digest
                        && entry.byte_size == original.byte_size
                }) {
                    return Err(
                        "lineage differs from its complete original parent ownership".into(),
                    );
                }
                dependencies.push(original);
            }
        }
        dependencies
            .sort_by(|left, right| (left.kind, &left.selector).cmp(&(right.kind, &right.selector)));
        if let Some(original) = retained.record(manifest.selector()) {
            if original.retirement.is_some()
                || original.tenant_id != source.tenant_id
                || original.principal_id != source.principal_id
                || original.dependencies != dependencies
            {
                return Err("publication changes or revives original Artifact ownership".into());
            }
            return Ok(Vec::new());
        }
        drop(retained);
        if self.memory_source(manifest.selector())?.is_some() {
            return Err(
                "existing Catalog publication lacks original ownership; migration required".into(),
            );
        }
        let record = ArtifactCatalogReference {
            schema: "roboguide.artifact-catalog-reference/v0.1".into(),
            selector: manifest.selector().clone(),
            tenant_id: source.tenant_id.clone(),
            principal_id: source.principal_id.clone(),
            authority_digest: evidence_digest(&serde_json::json!({
                "source": source.authority_digest,
                "manifest": domain::memory_manifest_digest(manifest)?,
                "dependencies": dependencies,
            }))?,
            generation: 1,
            admitted_at_ms: now.as_millis(),
            dependencies,
            retirement: None,
        };
        record.validate()?;
        Ok(vec![EventPayload::ArtifactCatalogReferenceTransition {
            record,
        }])
    }

    /// Converts actual typed publication provenance without producing a generic manifest alias.
    pub(super) fn publication_events(
        &self,
        payload: &EventPayload,
        source: Option<&ArtifactPublicationSource>,
        now: TimestampMs,
    ) -> Result<Vec<EventPayload>, String> {
        if self.retained_store.is_none() {
            return Ok(Vec::new());
        }
        let (manifest, parents, typed_map) = match payload {
            EventPayload::MemoryManifestPublished { manifest } => {
                (manifest.clone(), Vec::new(), None)
            }
            EventPayload::MapArtifactPublished { manifest } => {
                let mut parents = Vec::new();
                if let Some(parent) = manifest.parent_revision_id() {
                    let parent_selector = MapRevisionSelector::new(
                        manifest.selector().map_id().clone(),
                        parent.clone(),
                    );
                    let parent = self
                        .revision(&parent_selector)
                        .map_err(|error| error.message)?
                        .ok_or("typed map parent Catalog is unavailable")?;
                    parents.push(domain::map_memory_manifest(parent.manifest())?);
                }
                (
                    domain::map_memory_manifest(manifest)?,
                    parents,
                    Some(manifest),
                )
            }
            _ => return Ok(Vec::new()),
        };
        self.publication_reference_events(
            &manifest,
            &parents,
            typed_map,
            source.ok_or("retained publication requires original authenticated attribution")?,
            now,
        )
    }

    /// Installs only an original committed reference after application publication Commit.
    pub(crate) fn install_application_reference(
        &self,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        if self.retained_store.is_none() {
            return Ok(());
        }
        let record = self
            .event_log
            .artifact_catalog_reference(manifest.selector())
            .map_err(|error| error.to_string())?
            .ok_or("committed publication has no original Artifact reference")?;
        self.retention
            .lock()
            .map_err(|_| "Artifact retention projection unavailable")?
            .apply(&record)
    }

    /// Rejects fresh use of an irreversibly retired publication while preserving its policy history.
    pub(crate) fn require_live_map_publication(
        &self,
        map: &MapArtifactManifest,
    ) -> Result<(), String> {
        let selector = MemorySelector::new(
            MemoryId::new(map.selector().map_id().as_str()).map_err(|error| error.to_string())?,
            MemoryRevisionId::new(map.selector().revision_id().as_str())
                .map_err(|error| error.to_string())?,
        );
        self.require_live_publication(&selector)
    }

    /// Rejects fresh use of an irreversibly retired publication while preserving its policy history.
    pub(crate) fn require_live_publication(&self, selector: &MemorySelector) -> Result<(), String> {
        if self
            .retention
            .lock()
            .map_err(|_| "Artifact retention projection unavailable")?
            .is_retired(selector)
        {
            return Err("original Artifact Catalog publication is irreversibly retired".into());
        }
        Ok(())
    }
}
