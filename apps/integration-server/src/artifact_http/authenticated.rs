//! Request-scoped checks repeated under catalog ordering instead of trusting an earlier HTTP decision.

use super::*;

/// Original verified request context, never accepted from a JSON body or an authority-bearing header.
pub(super) struct AuthenticatedAdmission<'a> {
    /// Current application authority, sharing Control identity and membership.
    pub(super) authority: &'a dyn MemoryProviderAdmission,
    /// Actual TLS/source context retained while parsing or streaming a request.
    pub(super) principal: &'a ArtifactPrincipal,
    /// Immutable-byte ownership and protected-scope projection.
    pub(super) catalog: &'a ArtifactCatalog,
    /// Opaque original import identity, resolved through current Control authority rather than trusted.
    pub(super) import_id: Option<&'a str>,
}

impl MemoryProviderAdmission for AuthenticatedAdmission<'_> {
    /// Rechecks exact current TLS/grant interpretation through SQL Commit without catalog lock recursion.
    fn revalidate_artifact_request(&self) -> Result<(), String> {
        self.authority.revalidate_artifact_principal(self.principal)
    }
    /// Keeps retirement proof on actual original Control/application history after current TLS checks.
    fn catalog_retirement_proof(
        &self,
        record: &domain::ArtifactCatalogReference,
    ) -> Result<String, String> {
        self.authority
            .revalidate_artifact_principal(self.principal)?;
        if self.principal.grant.role != ArtifactRole::Admin
            || record.tenant_id != self.principal.tenant_id
        {
            return Err(
                "Catalog retirement requires the original tenant's actual administrator".into(),
            );
        }
        self.authority.catalog_retirement_proof(record)
    }
    /// Freezes actual transport and committed grant interpretation, never caller-declared headers.
    fn publication_source(&self) -> Result<ArtifactPublicationSource, String> {
        self.authority
            .revalidate_artifact_principal(self.principal)?;
        Ok(ArtifactPublicationSource {
            tenant_id: self.principal.tenant_id.clone(),
            principal_id: self.principal.subject.clone(),
            authority_digest: super::retention::evidence_digest(&serde_json::json!({
                "principal": self.principal.subject,
                "tenant": self.principal.tenant_id,
                "certificate": self.principal.certificate_digest,
                "registry": self.principal.registry_id,
                "revision": self.principal.registry_revision,
                "grant": self.principal.grant,
            }))?,
        })
    }
    /// Rejects remote legacy claims and binds current producer/provider to the actual certificate.
    fn admit_map_publication(
        &self,
        manifest: &MapArtifactManifest,
        publisher: Option<&MemoryPublicationIdentity>,
        execution_id: Option<&str>,
    ) -> Result<(), String> {
        self.authority
            .revalidate_artifact_principal(self.principal)?;
        if self.principal.certificate_digest.is_some()
            && (manifest.producer_provider_id().is_none()
                || self.principal.node_id() != Some(manifest.producer_node_id())
                || !self
                    .principal
                    .grant
                    .provider_ids
                    .contains(manifest.producer_provider_id().unwrap_or_default()))
        {
            return Err("remote typed-map publication requires its actual certificate Node and registered provider".into());
        }
        self.authority
            .admit_map_publication(manifest, publisher, execution_id)
    }
    /// Resolves the actual certificate through the same composition authority as the original request.
    fn refresh_artifact_principal(
        &self,
        principal: &ArtifactPrincipal,
    ) -> Result<ArtifactPrincipal, String> {
        self.authority.refresh_artifact_principal(principal)
    }
    /// Delegates current source-bound identity revalidation without falling back to a static profile.
    fn revalidate_artifact_principal(&self, principal: &ArtifactPrincipal) -> Result<(), String> {
        self.authority.revalidate_artifact_principal(principal)
    }
    /// Rechecks original principal, exact byte ownership and current semantic access at Commit ordering.
    fn admit_artifact_payload(&self, payload: &EventPayload) -> Result<(), String> {
        self.authority
            .revalidate_artifact_principal(self.principal)?;
        self.authority.admit_artifact_payload(payload)?;
        match payload {
            EventPayload::ArtifactUploadRecoveryTransition { record } => {
                record.validate()?;
                if record.upload.principal_id != self.principal.subject
                    || record.upload.tenant_id != self.principal.tenant_id
                    || record.replacement_admission.original_certificate
                        != self
                            .principal
                            .certificate_digest
                            .clone()
                            .unwrap_or_else(|| self.principal.subject.clone())
                {
                    return Err(
                        "recovery differs from its actual original uploader/tenant/leaf".into(),
                    );
                }
                self.catalog
                    .replica_content
                    .as_ref()
                    .ok_or("explicit recovery client absent")?
                    .check_upload_recovery(record)?;
            }
            EventPayload::ArtifactUploadRetired { receipt } => {
                receipt.validate()?;
                if receipt.principal_id != self.principal.subject
                    || receipt.tenant_id != self.principal.tenant_id
                    || receipt.certificate_digest != self.principal.certificate_digest
                    || (self.principal.grant.role != ArtifactRole::Admin
                        && self.principal.subject != receipt.upload.principal_id)
                    || self
                        .catalog
                        .retained_store
                        .as_ref()
                        .and_then(|store| store.owner_id())
                        != Some(receipt.store_owner_id.as_str())
                {
                    return Err(
                        "upload retirement differs from its actual current tenant/owner".into(),
                    );
                }
            }
            EventPayload::ArtifactCatalogLegacyAdopted { receipt } => {
                receipt.validate()?;
                if self.principal.grant.role != ArtifactRole::Admin
                    || receipt.authority.principal_id != self.principal.subject
                    || receipt.authority.tenant_id != self.principal.tenant_id
                    || receipt.authority.certificate_digest != self.principal.certificate_digest
                {
                    return Err(
                        "legacy Catalog adoption differs from its actual current administrator"
                            .into(),
                    );
                }
                let (manifest, _) = receipt.publication.manifest()?;
                self.authority.authorize_memory_access(
                    self.principal,
                    &manifest,
                    domain::MemoryAccessOperation::Discover,
                    None,
                )?;
            }
            EventPayload::ArtifactUploadTransition { record } => {
                if record.principal_id != self.principal.subject
                    || record.tenant_id != self.principal.tenant_id
                {
                    return Err("upload transition crosses original authenticated owner".into());
                }
                let recoveries = self
                    .catalog
                    .upload_recoveries
                    .lock()
                    .map_err(|_| "recovery evidence unavailable")?;
                if let Some(original) = recoveries
                    .values()
                    .find(|r| r.replacement_admission.upload_id == record.upload_id)
                {
                    self.catalog
                        .replica_content
                        .as_ref()
                        .ok_or("explicit recovery client absent")?
                        .check_upload_recovery(original)?;
                }
            }
            EventPayload::MemoryManifestPublished { manifest } => {
                self.catalog.require_live_publication(manifest.selector())?;
                self.catalog.authorize_publication_bytes(
                    self.principal,
                    manifest
                        .artifact()
                        .map(|a| (a.content_digest().as_str(), a.byte_size())),
                    Some(manifest),
                    self.authority,
                )?;
            }
            EventPayload::MapArtifactPublished { manifest } => {
                self.catalog.require_live_map_publication(manifest)?;
                self.catalog.authorize_publication_bytes(
                    self.principal,
                    Some((
                        manifest.artifact().content_digest().as_str(),
                        manifest.artifact().byte_size(),
                    )),
                    None,
                    self.authority,
                )?;
            }
            EventPayload::MemoryArtifactStaged { manifest, .. }
            | EventPayload::MemoryArtifactImported { manifest, .. }
            | EventPayload::MemoryArtifactRejected { manifest, .. } => {
                self.catalog.require_live_publication(manifest.selector())?;
                self.authority.authorize_memory_access(
                    self.principal,
                    manifest,
                    domain::MemoryAccessOperation::Import,
                    self.import_id,
                )?;
            }
            EventPayload::MapMemoryReplicaRecorded { manifest, .. } => {
                self.catalog.require_live_map_publication(manifest)?;
                if self.import_id.is_none() {
                    return Err(
                        "typed-map byte placement requires its original prepared purpose".into(),
                    );
                }
                self.authority.authorize_map_access(
                    self.principal,
                    manifest,
                    domain::MemoryAccessOperation::Import,
                    self.import_id,
                )?;
            }
            _ => {}
        }
        Ok(())
    }

    /// Keeps provider declaration authority in the composition adapter.
    fn admit_manifest(&self, manifest: &MemoryArtifactManifest) -> Result<(), String> {
        self.authority.admit_manifest(manifest)
    }
    /// Delegates explicit source-bound scope handoff to the current Control authority.
    fn authorize_memory_handoff(
        &self,
        principal: &ArtifactPrincipal,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        self.authority
            .authorize_memory_handoff(principal, parent, child)
    }
    /// Keeps exact receiving provider and Node checks in the composition adapter.
    fn admit_replica(
        &self,
        node: &NodeId,
        provider: &str,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        self.authority.admit_replica(node, provider, manifest)
    }
    /// Rechecks actual certificate revocation before the existing current-session proof.
    fn admit_publisher(
        &self,
        publisher: Option<&MemoryPublicationIdentity>,
        node: &NodeId,
    ) -> Result<(), String> {
        self.authority
            .revalidate_artifact_principal(self.principal)?;
        self.authority.admit_publisher(publisher, node)
    }
    /// Preserves the exact current Runtime attempt admission boundary.
    fn admit_localization_evidence(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        self.authority.admit_localization_evidence(evidence)
    }
}
