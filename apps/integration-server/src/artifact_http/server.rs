//! Artifact HTTP request routing and catalog mutation endpoints.

use super::*;

/// Certificate-to-role profile for a remotely bound Artifact HTTP listener.
#[derive(Clone)]
pub struct ArtifactTlsProfile {
    /// Mutual-TLS acceptor requiring a client certificate signed by the configured CA.
    acceptor: TlsAcceptor,
    /// Certificate digest to bounded artifact role mapping.
    roles: Arc<BTreeMap<String, ArtifactGrant>>,
}

impl ArtifactTlsProfile {
    /// Resolve the certificate principal from one accepted TLS stream.
    fn principal<S>(
        &self,
        stream: &tokio_rustls::server::TlsStream<S>,
    ) -> Result<ArtifactPrincipal, String> {
        let certificates = stream
            .get_ref()
            .1
            .peer_certificates()
            .ok_or("Artifact mTLS peer certificate is missing")?;
        let certificate = certificates
            .first()
            .ok_or("Artifact mTLS peer certificate chain is empty")?;
        let digest = format!("sha256:{:x}", Sha256::digest(certificate.as_ref()));
        self.roles
            .get(&digest)
            .cloned()
            .map(|grant| ArtifactPrincipal {
                subject: digest,
                grant,
            })
            .ok_or_else(|| "Artifact certificate is not authorized".to_string())
    }
}

/// Load the optional Artifact mTLS profile from startup-owned environment variables.
pub fn configured_artifact_tls() -> Result<Option<ArtifactTlsProfile>, Box<dyn std::error::Error>> {
    let names = [
        "ROBOGUIDE_ARTIFACT_MTLS_SERVER_CERT",
        "ROBOGUIDE_ARTIFACT_MTLS_SERVER_KEY",
        "ROBOGUIDE_ARTIFACT_MTLS_CLIENT_CA",
        "ROBOGUIDE_ARTIFACT_MTLS_ROLE_MAP",
    ];
    let values = names.iter().map(std::env::var_os).collect::<Vec<_>>();
    if values.iter().all(Option::is_none) {
        return Ok(None);
    }
    if values.iter().any(Option::is_none) {
        return Err(
            "Artifact mTLS requires server cert, server key, client CA and role map".into(),
        );
    }
    let certificate_path = std::path::Path::new(values[0].as_ref().expect("validated path"));
    let key_path = std::path::Path::new(values[1].as_ref().expect("validated path"));
    let ca_path = std::path::Path::new(values[2].as_ref().expect("validated path"));
    let role_map_path = std::path::Path::new(values[3].as_ref().expect("validated path"));
    load_artifact_tls(certificate_path, key_path, ca_path, role_map_path).map(Some)
}

/// Loads the same startup-frozen certificate grants without process-global environment mutation.
pub(super) fn load_artifact_tls(
    certificate_path: &std::path::Path,
    key_path: &std::path::Path,
    ca_path: &std::path::Path,
    role_map_path: &std::path::Path,
) -> Result<ArtifactTlsProfile, Box<dyn std::error::Error>> {
    let server_cert = load_certificates(certificate_path)?;
    let server_key = load_private_key(key_path)?;
    let ca = load_certificates(ca_path)?;
    let raw_roles: BTreeMap<String, serde_json::Value> =
        serde_json::from_slice(&std::fs::read(role_map_path)?)?;
    if raw_roles.is_empty() {
        return Err("Artifact mTLS role map must not be empty".into());
    }
    let roles = raw_roles
        .into_iter()
        .map(|(digest, role)| {
            let hex = digest.strip_prefix("sha256:").ok_or_else(|| {
                "Artifact mTLS role map keys must use sha256:<64 lowercase hex>".to_string()
            })?;
            if hex.len() != 64
                || !hex
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            {
                return Err("Artifact mTLS role map contains an invalid certificate digest".into());
            }
            Ok((digest, ArtifactGrant::parse(role)?))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut roots = rustls::RootCertStore::empty();
    for certificate in ca {
        roots.add(certificate)?;
    }
    let verifier = rustls::server::WebPkiClientVerifier::builder(Arc::new(roots)).build()?;
    let server = rustls::ServerConfig::builder()
        .with_client_cert_verifier(verifier)
        .with_single_cert(server_cert, server_key)?;
    Ok(ArtifactTlsProfile {
        acceptor: TlsAcceptor::from(Arc::new(server)),
        roles: Arc::new(roles),
    })
}

/// Load a bounded PEM certificate chain.
pub(super) fn load_certificates(
    path: &std::path::Path,
) -> Result<Vec<CertificateDer<'static>>, Box<dyn std::error::Error>> {
    let mut reader = BufReader::new(std::fs::File::open(path)?);
    Ok(rustls_pemfile::certs(&mut reader).collect::<Result<Vec<_>, _>>()?)
}

/// Load the first supported PEM private key.
pub(super) fn load_private_key(
    path: &std::path::Path,
) -> Result<PrivateKeyDer<'static>, Box<dyn std::error::Error>> {
    let mut reader = BufReader::new(std::fs::File::open(path)?);
    Ok(rustls_pemfile::private_key(&mut reader)?.ok_or("Artifact mTLS private key is empty")?)
}

/// Accepts independent artifact HTTP connections from an already-bound listener forever.
pub async fn serve_artifact_http(
    listener: TcpListener,
    store: FileSystemArtifactStore,
    catalog: ArtifactCatalog,
    memory_admission: Arc<dyn MemoryProviderAdmission>,
    localization_observer: Arc<dyn LocalizationEvidenceObserver>,
    tls: Option<ArtifactTlsProfile>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let uploads: Uploads = Arc::new(Mutex::new(UploadRegistry::production()));
    let connections = Arc::new(tokio::sync::Semaphore::new(128));
    let mut sweep = interval(UPLOAD_SWEEP_INTERVAL);
    sweep.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (mut stream, _) = accepted?;
                let Ok(permit) = Arc::clone(&connections).try_acquire_owned() else { continue; };
                let shared_store = store.clone();
                let shared_catalog = catalog.clone();
                let shared_uploads = uploads.clone();
                let shared_memory_admission = Arc::clone(&memory_admission);
                let shared_localization_observer = Arc::clone(&localization_observer);
                let tls = tls.clone();
                tokio::spawn(async move {
                    let _permit = permit;
                    if let Some(profile) = tls {
                        let Ok(accepted) = timeout(Duration::from_secs(5), profile.acceptor.accept(stream)).await else { return; };
                        match accepted {
                            Ok(mut tls_stream) => {
                                let role = match profile.principal(&tls_stream) {
                                    Ok(role) => role,
                                    Err(error) => {
                                        eprintln!("Artifact mTLS identity rejected: {error}");
                                        return;
                                    }
                                };
                                if let Err(error) = handle_authenticated_connection(
                                    &mut tls_stream,
                                    &shared_store,
                                    &shared_catalog,
                                    &shared_uploads,
                                    shared_memory_admission.as_ref(),
                                    shared_localization_observer.as_ref(),
                                    role,
                                ).await {
                                    let _ = write_json(&mut tls_stream, error.status(), &error.body()).await;
                                }
                            }
                            Err(error) => eprintln!("Artifact mTLS handshake failed: {error}"),
                        }
                    } else if let Err(error) = handle_authenticated_connection(
                        &mut stream,
                        &shared_store,
                        &shared_catalog,
                        &shared_uploads,
                        shared_memory_admission.as_ref(),
                        shared_localization_observer.as_ref(),
                        ArtifactPrincipal::local(),
                    ).await {
                        let _ = write_json(&mut stream, error.status(), &error.body()).await;
                    }
                });
            }
            _ = sweep.tick() => {
                if let Err(error) = expire_uploads(&uploads, Instant::now()) {
                    eprintln!("artifact upload expiration failed: {}", error.message);
                }
            }
        }
    }
}

/// Handles one request and closes the short-lived HTTP connection.
#[cfg(test)]
pub(super) async fn handle_connection(
    stream: &mut TcpStream,
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    memory_admission: &dyn MemoryProviderAdmission,
    localization_observer: &dyn LocalizationEvidenceObserver,
) -> Result<(), HttpError> {
    handle_authenticated_connection(
        stream,
        store,
        catalog,
        uploads,
        memory_admission,
        localization_observer,
        ArtifactPrincipal::local(),
    )
    .await
}

/// Handles one authenticated Artifact request over raw or TLS transport.
async fn handle_authenticated_connection<S>(
    stream: &mut S,
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    memory_admission: &dyn MemoryProviderAdmission,
    localization_observer: &dyn LocalizationEvidenceObserver,
    principal: ArtifactPrincipal,
) -> Result<(), HttpError>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let mut head = read_request_head(stream).await?;
    let method = head.method.clone();
    let path = head.path.clone();
    let authorization = principal.authorize_head(&head, catalog);
    catalog.audit_access(
        &principal.subject,
        &method,
        &path,
        if authorization.is_ok() {
            "route_allowed"
        } else {
            "route_denied"
        },
    )?;
    authorization?;
    let response = match (method.as_str(), path.as_str()) {
        ("GET", "/healthz") => {
            catalog.ensure_available()?;
            Response::Json("200 OK", serde_json::json!({"status": "ok"}))
        }
        ("GET", "/readyz") => {
            catalog.readiness()?;
            Response::Json("200 OK", serde_json::json!({"status": "ok", "ready": true}))
        }
        ("GET", "/metrics") => artifact_metrics(catalog, uploads)?,
        ("POST", "/v1/artifact-uploads") => {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            start_upload(&request, store, uploads)?
        }
        ("GET", "/v1/maps") => {
            let revisions = catalog.revisions()?;
            Response::Json("200 OK", serde_json::json!({"revisions": revisions}))
        }
        ("GET", "/v1/memories") => list_memories(catalog)?,
        ("GET", path) if path.starts_with("/v1/artifacts/") => {
            stream_artifact(stream, store, path.trim_start_matches("/v1/artifacts/")).await?;
            return Ok(());
        }
        ("GET", path) if path.starts_with("/v1/maps/") => get_revision(catalog, path)?,
        ("GET", path) if path.starts_with("/v1/memories/") => get_memory(catalog, path)?,
        (method, path)
            if matches!(method, "POST" | "PUT")
                && path.starts_with("/v1/artifact-uploads/")
                && path.ends_with("/content") =>
        {
            append_upload(stream, &head, uploads).await?
        }
        ("POST", path)
            if path.starts_with("/v1/artifact-uploads/") && path.ends_with("/finalize") =>
        {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            finalize_upload(&request, store, uploads)?
        }
        ("DELETE", path) if path.starts_with("/v1/artifact-uploads/") => {
            abort_upload(path, uploads)?
        }
        ("POST", path)
            if path.starts_with("/v1/maps/") && path.ends_with("/localization-evidence") =>
        {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            record_localization_evidence(
                catalog,
                memory_admission,
                localization_observer,
                &request,
                path,
            )?
        }
        ("POST", path) if path.starts_with("/v1/maps/") && path.ends_with("/replicas") => {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            record_replica(catalog, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/memories/") && path.ends_with("/replicas") => {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            record_memory_replica(catalog, store, memory_admission, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/memories/") && path.contains("/revisions/") => {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            publish_memory(catalog, store, memory_admission, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/maps/") && path.contains("/revisions/") => {
            let request = head.read_json(stream).await?;
            principal.authorize_body(&request)?;
            publish_revision(catalog, store, &request, path)?
        }
        _ => return Err(HttpError::not_found("artifact endpoint not found")),
    };
    response.write(stream).await
}

/// Returns bounded Artifact/Memory catalog metrics for readiness and SLO collection.
fn artifact_metrics(catalog: &ArtifactCatalog, uploads: &Uploads) -> Result<Response, HttpError> {
    let memories = catalog.memories()?.len();
    let maps = catalog.revisions()?.len();
    let active_uploads = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload registry lock is poisoned"))?
        .active_uploads();
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({
            "schema": "roboguide.artifact-metrics/v0.1",
            "memory_manifests": memories,
            "map_revisions": maps,
            "active_uploads": active_uploads,
        }),
    ))
}

/// Lists generic Memory plus a read-only adapter over typed Spatial map revisions.
pub(super) fn list_memories(catalog: &ArtifactCatalog) -> Result<Response, HttpError> {
    let mut memories = catalog
        .memories()?
        .into_iter()
        .map(|manifest| {
            let key = (
                manifest.selector().memory_id().as_str().to_string(),
                manifest.selector().revision_id().as_str().to_string(),
            );
            serde_json::to_value(manifest)
                .map(|value| (key, value))
                .map_err(|error| HttpError::internal(format!("encode Memory manifest: {error}")))
        })
        .collect::<Result<Vec<_>, _>>()?;
    memories.extend(
        catalog
            .revisions()?
            .into_iter()
            .map(|snapshot| {
                let selector = snapshot.manifest().selector();
                let key = (
                    selector.map_id().as_str().to_string(),
                    selector.revision_id().as_str().to_string(),
                );
                (key, map_memory_view(snapshot))
            })
            .collect::<Vec<_>>(),
    );
    memories.sort_by(|left, right| left.0.cmp(&right.0));
    let memories = memories
        .into_iter()
        .map(|(_, value)| value)
        .collect::<Vec<_>>();
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({
            "schema": "roboguide.memory-catalog/v0.1",
            "memories": memories,
        }),
    ))
}

/// Returns one generic Memory manifest and its node-local replica evidence.
pub(super) fn get_memory(catalog: &ArtifactCatalog, path: &str) -> Result<Response, HttpError> {
    let selector = memory_selector_from_path(path, false)?;
    if let Some(manifest) = catalog.memory(&selector)? {
        let replicas = catalog.memory_replicas(&selector)?;
        return Ok(Response::Json(
            "200 OK",
            serde_json::json!({"manifest": manifest, "replicas": replicas}),
        ));
    }
    let map_selector = map_selector_from_memory(&selector)?;
    let snapshot = catalog
        .revision(&map_selector)?
        .ok_or_else(|| HttpError::not_found("unknown Memory revision"))?;
    let replicas = catalog.map_replicas(&map_selector)?;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"manifest": map_memory_view(snapshot), "replicas": replicas}),
    ))
}

/// Publishes immutable Memory metadata after verifying any referenced CAS content.
pub(super) fn publish_memory(
    catalog: &ArtifactCatalog,
    store: &FileSystemArtifactStore,
    memory_admission: &dyn MemoryProviderAdmission,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = memory_selector_from_path(path, false)?;
    let manifest: MemoryArtifactManifest = parse_json(&request.body)?;
    manifest
        .validate()
        .map_err(|error| HttpError::bad_request(error.to_string()))?;
    if manifest.selector() != &selector {
        return Err(HttpError::bad_request(
            "Memory manifest selector does not match request path",
        ));
    }
    if manifest.kind() == domain::MemoryKind::Spatial
        && manifest.payload_schema() == domain::SPATIAL_MEMORY_SCHEMA_V0_1
    {
        return Err(HttpError::bad_request(
            "typed map manifests must use /v1/maps so Spatial validation remains authoritative",
        ));
    }
    if let Some(artifact) = manifest.artifact() {
        store
            .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size())
            .map_err(map_cas_error)?;
    }
    catalog.append_memory(
        EventPayload::MemoryManifestPublished { manifest },
        memory_admission,
        request.memory_publisher.as_ref(),
    )?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"status": "published"}),
    ))
}

/// Records one generic staged/imported/rejected exchange transition.
pub(super) fn record_memory_replica(
    catalog: &ArtifactCatalog,
    store: &FileSystemArtifactStore,
    memory_admission: &dyn MemoryProviderAdmission,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = memory_selector_from_path(path, true)?;
    let input: MemoryReplicaInput = parse_json(&request.body)?;
    if input.manifest.selector() != &selector {
        return Err(HttpError::bad_request(
            "Memory replica manifest selector does not match request path",
        ));
    }
    let artifact = input.manifest.artifact().ok_or_else(|| {
        HttpError::bad_request("metadata-only Memory cannot produce replica evidence")
    })?;
    store
        .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size())
        .map_err(map_cas_error)?;
    let node_id =
        NodeId::new(input.node_id).map_err(|error| HttpError::bad_request(error.to_string()))?;
    let consumer_provider_id = input.consumer_provider_id;
    let payload = match input.status.as_str() {
        "staged" => EventPayload::MemoryArtifactStaged {
            manifest: input.manifest,
            node_id,
            consumer_provider_id,
        },
        "imported" => EventPayload::MemoryArtifactImported {
            manifest: input.manifest,
            node_id,
            consumer_provider_id,
        },
        "rejected" => EventPayload::MemoryArtifactRejected {
            manifest: input.manifest,
            node_id,
            consumer_provider_id,
            reason: input
                .reason
                .unwrap_or_else(|| "rejected by node".to_string()),
        },
        _ => {
            return Err(HttpError::bad_request(
                "generic Memory replica status must be staged/imported/rejected",
            ));
        }
    };
    catalog.append_memory(payload, memory_admission, request.memory_publisher.as_ref())?;
    Ok(Response::Json(
        "202 Accepted",
        serde_json::json!({"status": input.status}),
    ))
}

/// Parses the fixed `/v1/memories/{id}/revisions/{revision}` resource grammar.
pub(super) fn memory_selector_from_path(
    path: &str,
    replica: bool,
) -> Result<MemorySelector, HttpError> {
    let parts = path
        .trim_start_matches("/v1/memories/")
        .split('/')
        .collect::<Vec<_>>();
    let expected_len = if replica { 4 } else { 3 };
    if parts.len() != expected_len || parts[1] != "revisions" || replica && parts[3] != "replicas" {
        return Err(HttpError::not_found("Memory revision path is invalid"));
    }
    Ok(MemorySelector::new(
        MemoryId::new(parts[0]).map_err(|error| HttpError::bad_request(error.to_string()))?,
        MemoryRevisionId::new(parts[2])
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    ))
}

/// Converts the shared path-safe Memory selector into its typed map counterpart.
pub(super) fn map_selector_from_memory(
    selector: &MemorySelector,
) -> Result<MapRevisionSelector, HttpError> {
    Ok(MapRevisionSelector::new(
        domain::MapId::new(selector.memory_id().as_str())
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
        domain::MapRevisionId::new(selector.revision_id().as_str())
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    ))
}

/// Adapts one typed map snapshot into generic Memory discovery JSON without duplicating facts.
pub(super) fn map_memory_view(snapshot: domain::MapRevisionSnapshot) -> serde_json::Value {
    let manifest = snapshot.manifest();
    serde_json::json!({
        "schema": "roboguide.memory-manifest-view/v0.1",
        "selector": {
            "memory_id": manifest.selector().map_id().as_str(),
            "revision_id": manifest.selector().revision_id().as_str(),
        },
        "kind": "spatial",
        "provider_id": "typed-map-catalog",
        "owner": {
            "owner": "node",
            "node_id": manifest.producer_node_id().as_str(),
            "local_system_id": manifest.producer_local_system_id().map(domain::LocalSystemId::as_str),
        },
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": domain::SPATIAL_MEMORY_SCHEMA_V0_1,
        "media_type": manifest.media_type(),
        "artifact": {
            "content_digest": manifest.artifact().content_digest(),
            "byte_size": manifest.artifact().byte_size(),
        },
        "source_mission_id": manifest.source_mission_id(),
        "source_execution_id": manifest.source_execution_id(),
        "source_task_ref": manifest.source_task_ref(),
        "created_at": manifest.created_at(),
        "typed_extension": "map",
        "status": snapshot.status(),
    })
}

/// Records one complete strong localization evidence event after path identity validation.
pub(super) fn record_localization_evidence(
    catalog: &ArtifactCatalog,
    admission: &dyn MemoryProviderAdmission,
    observer: &dyn LocalizationEvidenceObserver,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 4 || parts[1] != "revisions" || parts[3] != "localization-evidence" {
        return Err(HttpError::not_found(
            "localization evidence path is invalid",
        ));
    }
    let evidence: domain::LocalizationVerificationEvidence = parse_json(&request.body)?;
    if evidence.artifact().selector().map_id().as_str() != parts[0]
        || evidence.artifact().selector().revision_id().as_str() != parts[2]
    {
        return Err(HttpError::bad_request(
            "localization evidence selector does not match path",
        ));
    }
    let received_at = catalog.append_node_evidence(
        EventPayload::MapLocalizationEvidenceRecorded {
            evidence: evidence.clone(),
        },
        admission,
        request.memory_publisher.as_ref(),
        &evidence,
    )?;
    observer
        .observe(&evidence, received_at)
        .map_err(HttpError::service_unavailable)?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"status": "strongly-verified"}),
    ))
}

/// Starts one path-safe temporary upload and returns its opaque upload identity.
pub(super) fn start_upload(
    request: &Request,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
) -> Result<Response, HttpError> {
    let input: UploadStart = parse_json(&request.body)?;
    let now = Instant::now();
    let mut registry = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?;
    registry.expire_idle(now)?;
    if registry.active_uploads() >= registry.max_uploads {
        return Err(HttpError::resource_exhausted(
            "active artifact upload count exceeds limit",
        ));
    }
    if registry.sessions.contains_key(&input.upload_id) {
        return Err(HttpError::conflict("artifact upload id is already active"));
    }
    let upload = store
        .begin_upload(input.upload_id.clone())
        .map_err(map_cas_error)?;
    registry.insert_new(input.upload_id.clone(), upload, now)?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"upload_id": input.upload_id}),
    ))
}

/// Appends the streamed request body to one active upload without buffering the artifact.
pub(super) async fn append_upload(
    stream: &mut (impl tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin),
    head: &RequestHead,
    uploads: &Uploads,
) -> Result<Response, HttpError> {
    let upload_id = head
        .path
        .trim_start_matches("/v1/artifact-uploads/")
        .trim_end_matches("/content");
    if head.content_length > MAX_ARTIFACT_BYTES {
        return Err(HttpError::too_large("artifact exceeds v0 size limit"));
    }
    let mut in_flight = take_for_append(uploads, upload_id, head.content_length)?;
    let stream_result = timeout(
        UPLOAD_BODY_TIMEOUT,
        head.stream_body(stream, &mut in_flight.session.upload),
    )
    .await;
    match stream_result {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            abort_in_flight(uploads, in_flight)?;
            return Err(error);
        }
        Err(_) => {
            abort_in_flight(uploads, in_flight)?;
            return Err(HttpError::request_timeout(
                "artifact request body timed out",
            ));
        }
    }
    let size = in_flight.session.upload.size();
    restore_after_append(uploads, upload_id, in_flight)?;
    Ok(Response::Json(
        "202 Accepted",
        serde_json::json!({"upload_id": upload_id, "received_bytes": size}),
    ))
}

/// Finalizes one upload after validating the expected digest and byte count.
pub(super) fn finalize_upload(
    request: &Request,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
) -> Result<Response, HttpError> {
    let upload_id = request
        .path
        .trim_start_matches("/v1/artifact-uploads/")
        .trim_end_matches("/finalize");
    let input: UploadFinalize = parse_json(&request.body)?;
    let mut in_flight = take_for_terminal(uploads, upload_id)?;
    let artifact = match in_flight
        .session
        .upload
        .finalize(input.content_digest.as_str(), input.byte_size)
    {
        Ok(artifact) => artifact,
        Err(error) => {
            let response_error = map_cas_error(error);
            abort_in_flight(uploads, in_flight)?;
            return Err(response_error);
        }
    };
    release_in_flight(uploads, in_flight.accounted_bytes)?;
    let present = store.contains(artifact.digest()).map_err(map_cas_error)?;
    if !present {
        return Err(HttpError::internal(
            "finalized artifact disappeared from CAS",
        ));
    }
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"digest": artifact.digest(), "byte_size": artifact.size()}),
    ))
}

/// Explicitly aborts one staged upload and releases its count and byte quota.
pub(super) fn abort_upload(path: &str, uploads: &Uploads) -> Result<Response, HttpError> {
    let upload_id = path.trim_start_matches("/v1/artifact-uploads/");
    if upload_id.is_empty() || upload_id.contains('/') {
        return Err(HttpError::not_found("unknown artifact upload"));
    }
    let in_flight = take_for_terminal(uploads, upload_id)?;
    abort_in_flight(uploads, in_flight)?;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"upload_id": upload_id, "status": "aborted"}),
    ))
}

/// Streams one immutable CAS blob to the caller without loading it into memory.
pub(super) async fn stream_artifact(
    stream: &mut (impl tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin),
    store: &FileSystemArtifactStore,
    digest: &str,
) -> Result<(), HttpError> {
    let file = store.open_artifact(digest).map_err(map_cas_error)?;
    let metadata = file
        .metadata()
        .map_err(|error| HttpError::internal(format!("stat artifact: {error}")))?;
    let mut file = tokio::fs::File::from_std(file);
    let header = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        metadata.len()
    );
    stream
        .write_all(header.as_bytes())
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .await
            .map_err(|error| HttpError::internal(error.to_string()))?;
        if count == 0 {
            break;
        }
        stream
            .write_all(&buffer[..count])
            .await
            .map_err(|error| HttpError::internal(error.to_string()))?;
    }
    stream
        .shutdown()
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    Ok(())
}

/// Returns one manifest by its logical map/revision path.
pub(super) fn get_revision(catalog: &ArtifactCatalog, path: &str) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 3 || parts[1] != "revisions" {
        return Err(HttpError::not_found("map revision path is invalid"));
    }
    let selector = MapRevisionSelector::new(
        domain::MapId::new(parts[0]).map_err(|error| HttpError::bad_request(error.to_string()))?,
        domain::MapRevisionId::new(parts[2])
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    );
    match catalog.revision(&selector)? {
        Some(snapshot) => Ok(Response::Json("200 OK", serde_json::json!(snapshot))),
        None => Err(HttpError::not_found("unknown map revision")),
    }
}

/// Publishes a validated immutable manifest after confirming the CAS blob and byte count.
pub(super) fn publish_revision(
    catalog: &ArtifactCatalog,
    store: &FileSystemArtifactStore,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 3 || parts[1] != "revisions" {
        return Err(HttpError::not_found("map revision path is invalid"));
    }
    let manifest: MapArtifactManifest = parse_json(&request.body)?;
    if manifest.selector().map_id().as_str() != parts[0]
        || manifest.selector().revision_id().as_str() != parts[2]
    {
        return Err(HttpError::bad_request(
            "manifest selector does not match request path",
        ));
    }
    store
        .verify_artifact(
            manifest.artifact().content_digest().as_str(),
            manifest.artifact().byte_size(),
        )
        .map_err(map_cas_error)?;
    catalog.append(EventPayload::MapArtifactPublished { manifest })?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"status": "published"}),
    ))
}

/// Records one node-local staged/imported/verified/rejected replica evidence event.
pub(super) fn record_replica(
    catalog: &ArtifactCatalog,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 4 || parts[1] != "revisions" || parts[3] != "replicas" {
        return Err(HttpError::not_found("replica path is invalid"));
    }
    let path_revision = parts[2];
    let input: ReplicaInput = parse_json(&request.body)?;
    let manifest = input.manifest;
    if manifest.selector().map_id().as_str() != parts[0]
        || manifest.selector().revision_id().as_str() != path_revision
    {
        return Err(HttpError::bad_request(
            "replica manifest selector does not match path",
        ));
    }
    let node_id =
        NodeId::new(input.node_id).map_err(|error| HttpError::bad_request(error.to_string()))?;
    let mission_id = MissionId::new(input.mission_id)
        .map_err(|error| HttpError::bad_request(error.to_string()))?;
    let payload = match input.status.as_str() {
        "staged" => EventPayload::MapArtifactStaged {
            manifest,
            node_id,
            mission_id,
        },
        "imported" => EventPayload::MapArtifactImported {
            manifest,
            node_id,
            mission_id,
        },
        "verified" => EventPayload::MapLocalizationVerified {
            artifact: manifest.artifact().clone(),
            node_id,
            mission_id,
            anchor_id: SpatialAnchorId::new(input.anchor_id.unwrap_or_default())
                .map_err(|error| HttpError::bad_request(error.to_string()))?,
        },
        "rejected" => EventPayload::MapArtifactRejected {
            artifact: manifest.artifact().clone(),
            node_id,
            mission_id,
            reason: input
                .reason
                .unwrap_or_else(|| "rejected by node".to_string()),
        },
        _ => {
            return Err(HttpError::bad_request(
                "replica status must be staged/imported/verified/rejected",
            ));
        }
    };
    catalog.append(payload)?;
    Ok(Response::Json(
        "202 Accepted",
        serde_json::json!({"status": input.status}),
    ))
}

/// Expires idle sessions under one registry lock at a caller-supplied monotonic time.
pub(super) fn expire_uploads(uploads: &Uploads, now: Instant) -> Result<usize, HttpError> {
    uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?
        .expire_idle(now)
}

/// Extracts one upload while atomically reserving all bytes declared by its request body.
pub(super) fn take_for_append(
    uploads: &Uploads,
    upload_id: &str,
    incoming_bytes: u64,
) -> Result<InFlightUpload, HttpError> {
    let now = Instant::now();
    let mut registry = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?;
    registry.expire_idle(now)?;
    registry.take_for_append(upload_id, incoming_bytes)
}

/// Extracts one upload for a terminal operation after expiring stale idle sessions.
pub(super) fn take_for_terminal(
    uploads: &Uploads,
    upload_id: &str,
) -> Result<InFlightUpload, HttpError> {
    let now = Instant::now();
    let mut registry = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?;
    registry.expire_idle(now)?;
    registry.take_for_terminal(upload_id)
}

/// Returns a successfully streamed upload to the registry and refreshes its idle deadline.
pub(super) fn restore_after_append(
    uploads: &Uploads,
    upload_id: &str,
    in_flight: InFlightUpload,
) -> Result<(), HttpError> {
    uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?
        .restore_after_append(upload_id.to_string(), in_flight, Instant::now())
}

/// Releases quota for one terminal upload after finalization has removed its staging name.
pub(super) fn release_in_flight(uploads: &Uploads, accounted_bytes: u64) -> Result<(), HttpError> {
    uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?
        .release_in_flight(accounted_bytes)
}

/// Explicitly aborts an extracted upload, then releases its reserved count and bytes.
pub(super) fn abort_in_flight(
    uploads: &Uploads,
    mut in_flight: InFlightUpload,
) -> Result<(), HttpError> {
    let abort_result = in_flight.session.upload.abort().map_err(map_cas_error);
    release_in_flight(uploads, in_flight.accounted_bytes)?;
    abort_result
}
