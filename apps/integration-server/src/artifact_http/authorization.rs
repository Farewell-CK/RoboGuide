//! Certificate-bound resource authorization, separate from Node/session evidence admission.

use super::*;
use std::collections::BTreeSet;

/// Explicit data-plane privileges; a writer does not gain administrator authority.
#[derive(Clone, Copy, PartialEq, Eq, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub(super) enum ArtifactRole {
    /// Read only explicitly granted resources.
    Viewer,
    /// Publish only the mapped Node/provider's granted resources.
    Writer,
    /// Deployment administrator with complete data-plane access.
    Admin,
}

/// Startup-frozen certificate grant; empty resource sets grant no data access.
#[derive(Clone, Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ArtifactGrant {
    /// Bounded data-plane role.
    pub(super) role: ArtifactRole,
    /// Node whose evidence this certificate may publish.
    #[serde(default)]
    pub(super) node_id: Option<NodeId>,
    /// Provider identities whose Memory this Node may publish or import.
    #[serde(default)]
    pub(super) provider_ids: BTreeSet<String>,
    /// Exact generic Memory identities readable or writable by the certificate.
    #[serde(default)]
    pub(super) memory_ids: BTreeSet<String>,
    /// Exact typed map identities readable by the certificate.
    #[serde(default)]
    pub(super) map_ids: BTreeSet<String>,
    /// Explicit blob grants, additional to blobs referenced by permitted manifests.
    #[serde(default)]
    pub(super) artifact_digests: BTreeSet<String>,
}

impl From<&domain::ArtifactCertificateGrant> for ArtifactGrant {
    /// Projects only committed data-plane privileges; stable attribution stays on the request context.
    fn from(grant: &domain::ArtifactCertificateGrant) -> Self {
        Self {
            role: match grant.role {
                domain::ArtifactAccessRole::Viewer => ArtifactRole::Viewer,
                domain::ArtifactAccessRole::Writer => ArtifactRole::Writer,
                domain::ArtifactAccessRole::Admin => ArtifactRole::Admin,
            },
            node_id: grant.node_id.clone(),
            provider_ids: grant.provider_ids.clone(),
            memory_ids: grant.memory_ids.clone(),
            map_ids: grant.map_ids.clone(),
            artifact_digests: grant.artifact_digests.clone(),
        }
    }
}

impl ArtifactGrant {
    /// Reads legacy roles conservatively and requires a Node/provider identity for writers.
    pub(super) fn parse(value: serde_json::Value) -> Result<Self, String> {
        let value = if value.is_string() {
            serde_json::json!({"role":value})
        } else {
            value
        };
        let grant: Self = serde_json::from_value(value).map_err(|error| error.to_string())?;
        if grant.role == ArtifactRole::Writer
            && (grant.node_id.is_none() || grant.provider_ids.is_empty())
        {
            return Err("Artifact writer certificate requires node_id and provider_ids".into());
        }
        for items in [
            &grant.provider_ids,
            &grant.memory_ids,
            &grant.map_ids,
            &grant.artifact_digests,
        ] {
            if items.len() > 128
                || items
                    .iter()
                    .any(|item| item.trim().is_empty() || item.len() > 256 || item.contains('*'))
            {
                return Err("Artifact resource grants must be bounded exact identities".into());
            }
        }
        Ok(grant)
    }
}

/// Authenticated subject retained through authorization and durable audit.
#[derive(Clone)]
pub struct ArtifactPrincipal {
    /// Exact TLS leaf digest, or the explicit local development identity.
    pub(super) subject: String,
    /// Certificate-mapped permissions; never taken from request headers.
    pub(super) grant: ArtifactGrant,
    /// Original trusted namespace; compatibility grants use the explicit local deployment.
    pub(crate) tenant_id: String,
    /// Actual TLS identity; raw loopback has no peer leaf.
    pub(crate) certificate_digest: Option<String>,
    /// Original actual X.509 notAfter, independent of request headers and policy expiry.
    pub(crate) certificate_expires_at_ms: u64,
    /// Committed registry identity used for this request, if enrolled.
    pub(crate) registry_id: Option<String>,
    /// Exact grant interpretation checked again under the authority writer gate.
    pub(crate) registry_revision: Option<u64>,
    /// Whether a legacy startup role map explicitly contained this actual leaf.
    pub(crate) legacy_mapped: bool,
}

impl ArtifactPrincipal {
    /// Returns the certificate-mapped Node, independently of any publisher header.
    pub(crate) fn node_id(&self) -> Option<&NodeId> {
        self.grant.node_id.as_ref()
    }
    /// Checks an exact certificate-mapped provider rather than trusting a local request argument.
    pub(crate) fn provider_allowed(&self, provider: &str) -> bool {
        self.grant.provider_ids.contains(provider)
    }
    /// Creates the loopback-only compatibility administrator.
    pub(super) fn local() -> Self {
        Self {
            subject: "loopback:artifact-admin".into(),
            grant: ArtifactGrant::parse(serde_json::json!("admin")).expect("static role"),
            tenant_id: "local".into(),
            certificate_digest: None,
            certificate_expires_at_ms: u64::MAX,
            registry_id: None,
            registry_revision: None,
            legacy_mapped: true,
        }
    }

    /// Creates current request evidence from a committed registry and the original verified TLS leaf.
    pub(crate) fn with_committed_grant(
        &self,
        registry: &domain::ArtifactAuthorizationRegistry,
        grant: &domain::ArtifactCertificateGrant,
    ) -> Self {
        Self {
            subject: grant.principal_id.clone(),
            grant: grant.into(),
            tenant_id: grant.tenant_id.clone(),
            registry_id: Some(registry.registry_id.clone()),
            registry_revision: Some(registry.revision),
            ..self.clone()
        }
    }

    /// Fails closed for expired certificates and unmapped legacy leaves; current authority is separate.
    pub(crate) fn validate_transport(&self) -> Result<(), String> {
        if SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_millis()
            >= u128::from(self.certificate_expires_at_ms)
        {
            return Err("Artifact actual TLS leaf expired".into());
        }
        if self.registry_id.is_none() && !self.legacy_mapped {
            return Err("Artifact leaf is not explicitly enrolled".into());
        }
        Ok(())
    }

    /// Returns the stable authenticated audit identity without trusting body/header claims.
    pub(crate) fn subject(&self) -> &str {
        &self.subject
    }

    /// Checks route/resource ACLs before parsing a mutation body or streaming blob content.
    pub(super) fn authorize_head(
        &self,
        head: &RequestHead,
        catalog: &ArtifactCatalog,
    ) -> Result<(), HttpError> {
        if self.grant.role == ArtifactRole::Admin {
            return Ok(());
        }
        let path = head.path.as_str();
        let read = head.method == "GET";
        if !read && self.grant.role != ArtifactRole::Writer {
            return Err(HttpError::forbidden("Artifact certificate is read-only"));
        }
        if read && matches!(path, "/healthz" | "/readyz" | "/metrics") {
            return Ok(());
        }
        let allowed = if let Some(digest) = path.strip_prefix("/v1/artifacts/") {
            let digest = artifact_store::normalize_digest(digest).map_err(map_cas_error)?;
            read && (head.memory_import_id.is_some()
                || self.grant.artifact_digests.contains(&digest)
                || catalog.memories()?.iter().any(|manifest| {
                    self.grant
                        .memory_ids
                        .contains(manifest.selector().memory_id().as_str())
                        && manifest
                            .artifact()
                            .is_some_and(|artifact| artifact.content_digest().as_str() == digest)
                })
                || catalog.revisions()?.iter().any(|revision| {
                    self.grant
                        .map_ids
                        .contains(revision.manifest().selector().map_id().as_str())
                        && revision.manifest().artifact().content_digest().as_str() == digest
                }))
        } else if read && matches!(path, "/v1/memories" | "/v1/maps") {
            true
        } else if let Some(resource) = path.strip_prefix("/v1/memories/") {
            (read && head.memory_import_id.is_some())
                || self
                    .grant
                    .memory_ids
                    .contains(resource.split('/').next().unwrap_or_default())
        } else if let Some(resource) = path.strip_prefix("/v1/maps/") {
            self.grant
                .map_ids
                .contains(resource.split('/').next().unwrap_or_default())
                && (read || path.ends_with("/localization-evidence") || path.ends_with("/replicas"))
        } else if let Some(upload) = path.strip_prefix("/v1/artifact-uploads/") {
            let id = upload.split('/').next().unwrap_or_default();
            catalog
                .upload_record(id)?
                .is_some_and(|r| r.principal_id == self.subject && r.tenant_id == self.tenant_id)
        } else {
            !read && path == "/v1/artifact-uploads"
        };
        if !allowed {
            return Err(HttpError::forbidden(
                "Artifact resource is not granted to this authenticated principal",
            ));
        }
        if !read
            && (path.starts_with("/v1/memories/") || path.starts_with("/v1/maps/"))
            && head
                .memory_publisher
                .as_ref()
                .is_none_or(|publisher| Some(&publisher.node_id) != self.grant.node_id.as_ref())
        {
            return Err(HttpError::forbidden(
                "Artifact publisher differs from certificate Node",
            ));
        }
        Ok(())
    }

    /// Checks certificate ownership against the typed body before existing provider/session admission.
    pub(super) fn authorize_body(&self, request: &Request) -> Result<(), HttpError> {
        if self.grant.role == ArtifactRole::Admin {
            return Ok(());
        }
        if request.path == "/v1/artifact-uploads" {
            let input: UploadStart = parse_json(&request.body)?;
            if !input.upload_id.starts_with(&self.upload_prefix()) {
                return Err(HttpError::forbidden(
                    "upload identity must be certificate-scoped",
                ));
            }
        } else if request.path.starts_with("/v1/memories/") {
            if request.path.ends_with("/replicas") {
                let input: MemoryReplicaInput = parse_json(&request.body)?;
                if self.grant.node_id.as_ref().map(NodeId::as_str) != Some(input.node_id.as_str())
                    || !self
                        .grant
                        .provider_ids
                        .contains(&input.consumer_provider_id)
                {
                    return Err(HttpError::forbidden(
                        "Memory consumer Node/provider differs from certificate",
                    ));
                }
            } else {
                let manifest: MemoryArtifactManifest = parse_json(&request.body)?;
                if !matches!(manifest.owner(),MemoryOwner::Node{node_id,..} if Some(node_id)==self.grant.node_id.as_ref())
                    || !self.grant.provider_ids.contains(manifest.provider_id())
                {
                    return Err(HttpError::forbidden(
                        "Memory owner/provider differs from certificate",
                    ));
                }
            }
        } else if request.path.ends_with("/localization-evidence") {
            let evidence: domain::LocalizationVerificationEvidence = parse_json(&request.body)?;
            if Some(evidence.node_id()) != self.grant.node_id.as_ref() {
                return Err(HttpError::forbidden(
                    "localization owner differs from certificate",
                ));
            }
        } else if request.path.starts_with("/v1/maps/") && request.path.ends_with("/replicas") {
            let input: ReplicaInput = parse_json(&request.body)?;
            if self.grant.node_id.as_ref().map(NodeId::as_str) != Some(input.node_id.as_str()) {
                return Err(HttpError::forbidden(
                    "map replica Node differs from certificate",
                ));
            }
        }
        Ok(())
    }

    /// Namespaces unfinished uploads by the exact certificate to prevent cross-principal mutation.
    fn upload_prefix(&self) -> String {
        format!(
            "cert-{}-",
            self.certificate_digest
                .as_deref()
                .unwrap_or(&self.subject)
                .trim_start_matches("sha256:")
        )
    }
}
