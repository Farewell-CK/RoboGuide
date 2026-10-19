//! Durable source-bound upload receipts and protected-content reference authorization.

use super::server::{release_in_flight, restore_after_append, run_blocking, take_for_terminal};
use super::*;
use domain::{ArtifactUploadPhase, ArtifactUploadRecord};

/// Shared authority and storage handles for one staged upload finalization.
struct FinalizeContext<'a> {
    /// Filesystem CAS owner.
    store: &'a FileSystemArtifactStore,
    /// Process-local restored upload sessions.
    uploads: &'a Uploads,
    /// Durable upload event catalog.
    catalog: &'a ArtifactCatalog,
    /// Current authenticated admission authority.
    authority: &'a dyn MemoryProviderAdmission,
    /// Stable authenticated caller identity.
    principal: &'a ArtifactPrincipal,
}

/// Reads fresh Controller wall time; no peer or manifest timestamp can renew an upload budget.
fn now_ms() -> Result<u64, HttpError> {
    u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|e| HttpError::internal(e.to_string()))?
            .as_millis(),
    )
    .map_err(|e| HttpError::internal(e.to_string()))
}

/// Requires the original stable owner, Started phase and nonrenewable budget for new bytes.
pub(super) fn check_live_upload(
    catalog: &ArtifactCatalog,
    id: &str,
    principal: &ArtifactPrincipal,
) -> Result<ArtifactUploadRecord, HttpError> {
    let record = catalog.owned_upload(id, principal)?;
    if record.phase != ArtifactUploadPhase::Started || now_ms()? >= record.deadline_ms {
        return Err(HttpError::conflict(
            "upload is no longer within its original writable budget",
        ));
    }
    Ok(record)
}

/// Commits an upload command before staging; retry returns its original state without recreating files.
pub(super) fn start_authorized_upload(
    request: &Request,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
    catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
) -> Result<Response, HttpError> {
    let input: UploadStart = parse_json(&request.body)?;
    if let Some(record) = catalog.upload_record(&input.upload_id)? {
        authority
            .revalidate_artifact_principal(principal)
            .map_err(HttpError::forbidden)?;
        catalog.owned_upload(&input.upload_id, principal)?;
        if record.phase == ArtifactUploadPhase::Started {
            ensure_started_upload_session(
                &input.upload_id,
                store,
                uploads,
                catalog,
                authority,
                principal,
            )?;
        }
        return Ok(Response::Json(
            "200 OK",
            serde_json::json!({"upload_id":input.upload_id,"record":record,"replayed":true}),
        ));
    }
    {
        let registry = uploads
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload quota unavailable"))?;
        if registry.active_uploads() >= registry.max_uploads {
            return Err(HttpError::resource_exhausted(
                "active artifact upload count exceeds limit",
            ));
        }
    }
    let admitted_at_ms = now_ms()?;
    let record = ArtifactUploadRecord {
        upload_id: input.upload_id.clone(),
        principal_id: principal.subject.clone(),
        tenant_id: principal.tenant_id.clone(),
        original_certificate: principal
            .certificate_digest
            .clone()
            .unwrap_or_else(|| principal.subject.clone()),
        admitted_at_ms,
        deadline_ms: admitted_at_ms
            .checked_add(1_800_000)
            .ok_or_else(|| HttpError::internal("upload deadline overflow"))?,
        phase: ArtifactUploadPhase::Started,
        content_digest: None,
        byte_size: None,
        prepared_at_ms: None,
        completed_at_ms: None,
    };
    catalog.append_authorized(EventPayload::ArtifactUploadTransition { record }, authority)?;
    let _gate = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload writer ordering unavailable"))?;
    authority
        .revalidate_artifact_principal(principal)
        .map_err(HttpError::forbidden)?;
    // A failure leaves the original durable Started identity, never an invented successful upload.
    super::server::start_upload(request, store, uploads)
}

/// Recreates a missing Started upload session from its durable identity after restart.
fn ensure_started_upload_session(
    id: &str,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
    catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
) -> Result<(), HttpError> {
    check_live_upload(catalog, id, principal)?;
    if uploads
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload registry unavailable"))?
        .sessions
        .contains_key(id)
    {
        return Ok(());
    }
    let _gate = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload writer ordering unavailable"))?;
    authority
        .revalidate_artifact_principal(principal)
        .map_err(HttpError::forbidden)?;
    check_live_upload(catalog, id, principal)?;
    if uploads
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload registry unavailable"))?
        .sessions
        .contains_key(id)
    {
        return Ok(());
    }
    match run_blocking(|| store.resume_upload(id.to_string())) {
        Ok(upload) => uploads
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload registry unavailable"))?
            .insert_restored(upload, Instant::now()),
        Err(artifact_store::ArtifactStoreError::UploadNotFound { .. }) => {
            let upload =
                run_blocking(|| store.begin_upload(id.to_string())).map_err(map_cas_error)?;
            uploads
                .lock()
                .map_err(|_| HttpError::service_unavailable("upload registry unavailable"))?
                .insert_new(id.to_string(), upload, Instant::now())
        }
        Err(error) => Err(map_cas_error(error)),
    }
}

/// Finishes the exact staged proof, including a Prepared upload recovered after restart.
pub(super) fn finalize_authorized_upload(
    request: &Request,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
    catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
) -> Result<Response, HttpError> {
    let id = request
        .path
        .trim_start_matches("/v1/artifact-uploads/")
        .trim_end_matches("/finalize");
    let input: UploadFinalize = parse_json(&request.body)?;
    let digest =
        artifact_store::normalize_digest(input.content_digest.as_str()).map_err(map_cas_error)?;
    let mut record = catalog.owned_upload(id, principal)?;
    if record.phase == ArtifactUploadPhase::Abandoned {
        return Err(HttpError::conflict("artifact upload was abandoned"));
    }
    if record.phase != ArtifactUploadPhase::Started
        && (record.content_digest.as_deref() != Some(&digest)
            || record.byte_size != Some(input.byte_size))
    {
        return Err(HttpError::conflict(
            "finalize differs from the original immutable upload proof",
        ));
    }
    authority
        .revalidate_artifact_principal(principal)
        .map_err(HttpError::forbidden)?;

    let needs_staging = match record.phase {
        ArtifactUploadPhase::Started => {
            ensure_started_upload_session(id, store, uploads, catalog, authority, principal)?;
            true
        }
        ArtifactUploadPhase::Prepared => {
            match run_blocking(|| store.verify_artifact(&digest, input.byte_size)) {
                Ok(_) => false,
                Err(artifact_store::ArtifactStoreError::ArtifactNotFound { .. }) => true,
                Err(error) => return Err(map_cas_error(error)),
            }
        }
        ArtifactUploadPhase::Completed => {
            run_blocking(|| store.verify_artifact(&digest, input.byte_size))
                .map_err(|_| HttpError::conflict("completed artifact bytes are unavailable"))?;
            false
        }
        ArtifactUploadPhase::Abandoned => unreachable!(),
    };

    if needs_staging {
        let context = FinalizeContext {
            store,
            uploads,
            catalog,
            authority,
            principal,
        };
        record = finalize_staged_upload(id, record, digest.clone(), input.byte_size, &context)?;
    }
    if record.phase != ArtifactUploadPhase::Completed {
        record.phase = ArtifactUploadPhase::Completed;
        record.completed_at_ms = Some(now_ms()?);
        catalog.append_authorized(
            EventPayload::ArtifactUploadTransition {
                record: record.clone(),
            },
            authority,
        )?;
    }
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"digest":digest,"byte_size":input.byte_size,"receipt":record}),
    ))
}

/// Verifies, durably prepares, and finalizes a Started or Prepared staging handle.
fn finalize_staged_upload(
    id: &str,
    mut record: ArtifactUploadRecord,
    digest: String,
    byte_size: u64,
    context: &FinalizeContext<'_>,
) -> Result<ArtifactUploadRecord, HttpError> {
    let mut in_flight = take_for_terminal(context.uploads, id)?;
    let preparation = (|| {
        let _gate =
            context.catalog.write_gate.lock().map_err(|_| {
                HttpError::service_unavailable("upload writer ordering unavailable")
            })?;
        context
            .authority
            .revalidate_artifact_principal(context.principal)
            .map_err(HttpError::forbidden)?;
        if record.phase == ArtifactUploadPhase::Started {
            check_live_upload(context.catalog, id, context.principal)?;
        } else if context.catalog.owned_upload(id, context.principal)? != record {
            return Err(HttpError::conflict(
                "original prepared upload changed before finalization",
            ));
        }
        run_blocking(|| {
            in_flight
                .session
                .upload
                .prepare_finalize(&digest, byte_size)
        })
        .map_err(map_cas_error)
    })();
    if let Err(error) = preparation {
        if record.phase == ArtifactUploadPhase::Started && error.status == "409 Conflict" {
            in_flight.session.upload.abort().map_err(map_cas_error)?;
            release_in_flight(context.uploads, in_flight.accounted_bytes)?;
        } else {
            restore_after_append(context.uploads, id, in_flight)?;
        }
        return Err(error);
    }
    if record.phase == ArtifactUploadPhase::Started {
        record.phase = ArtifactUploadPhase::Prepared;
        record.content_digest = Some(digest.clone());
        record.byte_size = Some(byte_size);
        record.prepared_at_ms = Some(now_ms()?);
        if let Err(error) = context.catalog.append_authorized(
            EventPayload::ArtifactUploadTransition {
                record: record.clone(),
            },
            context.authority,
        ) {
            restore_after_append(context.uploads, id, in_flight)?;
            return Err(error);
        }
    }
    let result = (|| {
        let _gate =
            context.catalog.write_gate.lock().map_err(|_| {
                HttpError::service_unavailable("upload writer ordering unavailable")
            })?;
        context
            .authority
            .revalidate_artifact_principal(context.principal)
            .map_err(HttpError::forbidden)?;
        if context.catalog.owned_upload(id, context.principal)? != record {
            return Err(HttpError::conflict(
                "original upload changed before CAS finalization",
            ));
        }
        run_blocking(|| in_flight.session.upload.finalize(&digest, byte_size))
            .map_err(map_cas_error)
    })();
    if let Err(error) = result {
        restore_after_append(context.uploads, id, in_flight)?;
        return Err(error);
    }
    release_in_flight(context.uploads, in_flight.accounted_bytes)?;
    run_blocking(|| context.store.verify_artifact(&digest, byte_size)).map_err(map_cas_error)?;
    Ok(record)
}

/// Abandons only the original upload workflow; immutable CAS bytes and historical proof are retained.
pub(super) fn abandon_authorized_upload(
    path: &str,
    uploads: &Uploads,
    catalog: &ArtifactCatalog,
    authority: &dyn MemoryProviderAdmission,
    principal: &ArtifactPrincipal,
) -> Result<Response, HttpError> {
    let id = path.trim_start_matches("/v1/artifact-uploads/");
    let mut record = catalog.owned_upload(id, principal)?;
    if record.phase == ArtifactUploadPhase::Completed {
        return Err(HttpError::conflict(
            "completed upload bytes require the separate referenced-content GC contract",
        ));
    }
    if record.phase != ArtifactUploadPhase::Abandoned {
        record.phase = ArtifactUploadPhase::Abandoned;
        catalog.append_authorized(EventPayload::ArtifactUploadTransition { record }, authority)?;
    }
    let _gate = catalog
        .write_gate
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload writer ordering unavailable"))?;
    authority
        .revalidate_artifact_principal(principal)
        .map_err(HttpError::forbidden)?;
    if uploads
        .lock()
        .map_err(|_| HttpError::service_unavailable("upload registry unavailable"))?
        .sessions
        .contains_key(id)
    {
        super::server::abort_upload(path, uploads)?;
    }
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"upload_id":id,"status":"abandoned","cas_deleted":false}),
    ))
}

/// Refuses duplicate upload identities and any transition lacking its original byte/source proof.
pub(super) fn validate_record_transition(
    records: &BTreeMap<String, ArtifactUploadRecord>,
    record: &ArtifactUploadRecord,
) -> Result<(), String> {
    record.validate()?;
    if let Some(previous) = records.get(&record.upload_id) {
        previous.validates_successor(record)
    } else if record.phase != ArtifactUploadPhase::Started || records.len() >= 8192 {
        Err("upload history is full or lacks original admission".into())
    } else {
        Ok(())
    }
}

impl ArtifactCatalog {
    /// Returns upload records whose staging bytes may be resumed after a process restart.
    pub(super) fn recoverable_upload_records(
        &self,
    ) -> Result<Vec<ArtifactUploadRecord>, HttpError> {
        self.ensure_available()?;
        Ok(self
            .upload_records
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload evidence unavailable"))?
            .values()
            .filter(|record| {
                matches!(
                    record.phase,
                    ArtifactUploadPhase::Started | ArtifactUploadPhase::Prepared
                )
            })
            .cloned()
            .collect())
    }

    /// Reads original upload evidence without making a new upload or renewing any budget.
    pub(super) fn upload_record(
        &self,
        id: &str,
    ) -> Result<Option<ArtifactUploadRecord>, HttpError> {
        self.ensure_available()?;
        Ok(self
            .upload_records
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload evidence unavailable"))?
            .get(id)
            .cloned())
    }

    /// Returns whether this authenticated principal already supplied and finalized these bytes.
    ///
    /// The upload transition is the durable byte proof: `prepare_finalize` hashed and fsynced the
    /// staging inode before `Completed` was committed. Publication can therefore reuse that
    /// immutable evidence instead of hashing a multi-hundred-megabyte CAS blob a second time.
    pub(super) fn has_completed_upload(
        &self,
        principal: &ArtifactPrincipal,
        digest: &str,
        size: u64,
    ) -> Result<bool, HttpError> {
        self.ensure_available()?;
        let records = self
            .upload_records
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload evidence unavailable"))?;
        Ok(records.values().any(|record| {
            record.phase == ArtifactUploadPhase::Completed
                && record.principal_id == principal.subject
                && record.tenant_id == principal.tenant_id
                && record.content_digest.as_deref() == Some(digest)
                && record.byte_size == Some(size)
        }))
    }

    /// Returns whether the CAS digest has an earlier immutable upload or published manifest proof.
    pub(super) fn has_verified_content(&self, digest: &str, size: u64) -> Result<bool, HttpError> {
        self.ensure_available()?;
        let uploaded = self
            .upload_records
            .lock()
            .map_err(|_| HttpError::service_unavailable("upload evidence unavailable"))?
            .values()
            .any(|record| {
                record.phase == ArtifactUploadPhase::Completed
                    && record.content_digest.as_deref() == Some(digest)
                    && record.byte_size == Some(size)
            });
        if uploaded {
            return Ok(true);
        }
        let memory = self
            .memory_projection
            .lock()
            .map_err(|_| HttpError::service_unavailable("generic Memory catalog unavailable"))?;
        if memory.memories().into_iter().any(|manifest| {
            manifest.artifact().is_some_and(|artifact| {
                artifact.content_digest().as_str() == digest && artifact.byte_size() == size
            })
        }) {
            return Ok(true);
        }
        let projection = self
            .projection
            .lock()
            .map_err(|_| HttpError::service_unavailable("Spatial catalog unavailable"))?;
        Ok(projection.revisions().into_iter().any(|revision| {
            let artifact = revision.manifest().artifact();
            artifact.content_digest().as_str() == digest && artifact.byte_size() == size
        }))
    }

    /// Validates stable owner equality, including after leaf rotation; no prefix grants ownership.
    pub(super) fn owned_upload(
        &self,
        id: &str,
        principal: &ArtifactPrincipal,
    ) -> Result<ArtifactUploadRecord, HttpError> {
        let record = self
            .upload_record(id)?
            .ok_or_else(|| HttpError::not_found("unknown upload authority"))?;
        if record.principal_id != principal.subject || record.tenant_id != principal.tenant_id {
            return Err(HttpError::forbidden(
                "upload belongs to another authenticated principal/tenant",
            ));
        }
        Ok(record)
    }

    /// Requires actual supplied-byte receipt before referencing foreign CAS and preserves source scope.
    pub(super) fn authorize_publication_bytes(
        &self,
        principal: &ArtifactPrincipal,
        artifact: Option<(&str, u64)>,
        new_manifest: Option<&MemoryArtifactManifest>,
        authority: &dyn MemoryProviderAdmission,
    ) -> Result<(), String> {
        let Some((digest, size)) = artifact else {
            return Ok(());
        };
        let owns = self
            .upload_records
            .lock()
            .map_err(|_| "upload ownership evidence unavailable")?
            .values()
            .any(|r| {
                r.phase == ArtifactUploadPhase::Completed
                    && r.principal_id == principal.subject
                    && r.tenant_id == principal.tenant_id
                    && r.content_digest.as_deref() == Some(digest)
                    && r.byte_size == Some(size)
            });
        let originals = self
            .memories()
            .map_err(|e| e.message)?
            .into_iter()
            .filter(|m| {
                m.artifact()
                    .is_some_and(|a| a.content_digest().as_str() == digest)
            })
            .collect::<Vec<_>>();
        for original in &originals {
            let same = new_manifest.is_some_and(|next| next == original);
            let same_scope = new_manifest.is_some_and(|next| next.scope() == original.scope());
            if !same && !same_scope {
                let child =
                    new_manifest.ok_or("typed map alias cannot broaden protected Memory scope")?;
                authority.authorize_memory_handoff(principal, original, child)?;
            } else if !same && !owns {
                authority.authorize_memory_access(
                    principal,
                    original,
                    domain::MemoryAccessOperation::Handoff,
                    None,
                )?;
            }
        }
        // Explicit raw loopback is a trusted local composition boundary. Remote grants, including
        // administrators, never establish byte ownership from an existing digest or readable alias.
        if !owns && principal.certificate_digest.is_some() {
            return Err("publishing existing CAS requires an original completed upload byte-ownership receipt".into());
        }
        Ok(())
    }
}
