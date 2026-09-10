//! Bounded process-local lifecycle for incomplete artifact uploads.

use super::*;
/// One staged upload plus the last successful HTTP mutation time.
pub(super) struct UploadSession {
    /// CAS handle owning the incomplete staging file.
    pub(super) upload: ArtifactUpload,
    /// Monotonic process-local activity time used only for idle expiration.
    pub(super) last_activity: Instant,
}

/// Upload temporarily removed from the registry while one request mutates it.
pub(super) struct InFlightUpload {
    /// Session unavailable to concurrent requests until this operation completes.
    pub(super) session: UploadSession,
    /// Bytes reserved in the registry for this upload, including the incoming body.
    pub(super) accounted_bytes: u64,
}

/// Bounded process-local registry for incomplete HTTP uploads.
pub(super) struct UploadRegistry {
    /// Idle sessions keyed by safe caller-generated IDs.
    pub(super) sessions: BTreeMap<String, UploadSession>,
    /// Requests currently streaming, finalizing, or aborting an extracted session.
    pub(super) in_flight_count: usize,
    /// Staged bytes plus declared bytes reserved by active streaming requests.
    pub(super) active_bytes: u64,
    /// Hard count quota applied before a staging file is created.
    pub(super) max_uploads: usize,
    /// Hard aggregate byte quota applied before a body is streamed.
    pub(super) max_bytes: u64,
    /// Idle duration after which a staged session is explicitly aborted.
    pub(super) idle_ttl: Duration,
}

/// Shared bounded upload registry.
pub(super) type Uploads = Arc<Mutex<UploadRegistry>>;

impl UploadRegistry {
    /// Creates the production registry with fixed v0 resource limits.
    pub(super) fn production() -> Self {
        Self::with_limits(MAX_ACTIVE_UPLOADS, MAX_ACTIVE_UPLOAD_BYTES, UPLOAD_IDLE_TTL)
    }

    /// Creates an empty registry with explicit limits for deterministic policy tests.
    pub(super) fn with_limits(max_uploads: usize, max_bytes: u64, idle_ttl: Duration) -> Self {
        Self {
            sessions: BTreeMap::new(),
            in_flight_count: 0,
            active_bytes: 0,
            max_uploads,
            max_bytes,
            idle_ttl,
        }
    }

    /// Returns the number of idle and in-flight upload identities consuming quota.
    pub(super) fn active_uploads(&self) -> usize {
        self.sessions.len().saturating_add(self.in_flight_count)
    }

    /// Aborts sessions whose idle deadline has elapsed and releases their byte accounting.
    pub(super) fn expire_idle(&mut self, now: Instant) -> Result<usize, HttpError> {
        let expired = self
            .sessions
            .iter()
            .filter(|(_, session)| {
                now.saturating_duration_since(session.last_activity) >= self.idle_ttl
            })
            .map(|(upload_id, _)| upload_id.clone())
            .collect::<Vec<_>>();
        let mut first_error = None;
        for upload_id in &expired {
            let Some(mut session) = self.sessions.remove(upload_id) else {
                continue;
            };
            self.active_bytes = self.active_bytes.saturating_sub(session.upload.size());
            if let Err(error) = session.upload.abort()
                && first_error.is_none()
            {
                first_error = Some(map_cas_error(error));
            }
        }
        if let Some(error) = first_error {
            Err(error)
        } else {
            Ok(expired.len())
        }
    }

    /// Checks count/identity limits and inserts a newly created zero-byte CAS upload.
    pub(super) fn insert_new(
        &mut self,
        upload_id: String,
        upload: ArtifactUpload,
        now: Instant,
    ) -> Result<(), HttpError> {
        if self.active_uploads() >= self.max_uploads {
            return Err(HttpError::resource_exhausted(
                "active artifact upload count exceeds limit",
            ));
        }
        if self.sessions.contains_key(&upload_id) {
            return Err(HttpError::conflict("artifact upload id is already active"));
        }
        self.sessions.insert(
            upload_id,
            UploadSession {
                upload,
                last_activity: now,
            },
        );
        Ok(())
    }

    /// Extracts one session and reserves its declared incoming bytes atomically.
    pub(super) fn take_for_append(
        &mut self,
        upload_id: &str,
        incoming_bytes: u64,
    ) -> Result<InFlightUpload, HttpError> {
        let session = self
            .sessions
            .get(upload_id)
            .ok_or_else(|| HttpError::not_found("unknown artifact upload"))?;
        let upload_size = session.upload.size();
        let projected_upload = upload_size
            .checked_add(incoming_bytes)
            .ok_or_else(|| HttpError::too_large("artifact size overflows v0 limit"))?;
        if projected_upload > MAX_ARTIFACT_BYTES {
            return Err(HttpError::too_large("artifact exceeds v0 size limit"));
        }
        let projected_total = self
            .active_bytes
            .checked_add(incoming_bytes)
            .ok_or_else(|| HttpError::too_large("active artifact bytes overflow quota"))?;
        if projected_total > self.max_bytes {
            return Err(HttpError::too_large(
                "active artifact upload bytes exceed quota",
            ));
        }
        let session = self
            .sessions
            .remove(upload_id)
            .expect("checked upload session remains under registry lock");
        self.active_bytes = projected_total;
        self.in_flight_count = self.in_flight_count.saturating_add(1);
        Ok(InFlightUpload {
            session,
            accounted_bytes: projected_upload,
        })
    }

    /// Extracts a session for a terminal finalize or explicit abort operation.
    pub(super) fn take_for_terminal(
        &mut self,
        upload_id: &str,
    ) -> Result<InFlightUpload, HttpError> {
        let session = self
            .sessions
            .remove(upload_id)
            .ok_or_else(|| HttpError::not_found("unknown artifact upload"))?;
        let accounted_bytes = session.upload.size();
        self.in_flight_count = self.in_flight_count.saturating_add(1);
        Ok(InFlightUpload {
            session,
            accounted_bytes,
        })
    }

    /// Restores a completely streamed session while preserving its reserved byte accounting.
    pub(super) fn restore_after_append(
        &mut self,
        upload_id: String,
        mut in_flight: InFlightUpload,
        now: Instant,
    ) -> Result<(), HttpError> {
        if in_flight.session.upload.size() != in_flight.accounted_bytes {
            let release_result = self.release_in_flight(in_flight.accounted_bytes);
            let _ = in_flight.session.upload.abort();
            release_result?;
            return Err(HttpError::internal(
                "artifact upload byte accounting diverged",
            ));
        }
        if self.sessions.contains_key(&upload_id) {
            let release_result = self.release_in_flight(in_flight.accounted_bytes);
            let _ = in_flight.session.upload.abort();
            release_result?;
            return Err(HttpError::conflict(
                "artifact upload id became active concurrently",
            ));
        }
        in_flight.session.last_activity = now;
        self.in_flight_count = self.in_flight_count.saturating_sub(1);
        self.sessions.insert(upload_id, in_flight.session);
        Ok(())
    }

    /// Releases all count and byte quota reserved for one extracted terminal session.
    pub(super) fn release_in_flight(&mut self, accounted_bytes: u64) -> Result<(), HttpError> {
        let in_flight_count = self.in_flight_count.checked_sub(1).ok_or_else(|| {
            HttpError::internal("artifact upload in-flight accounting underflowed")
        })?;
        let active_bytes = self
            .active_bytes
            .checked_sub(accounted_bytes)
            .ok_or_else(|| HttpError::internal("artifact upload byte accounting underflowed"))?;
        self.in_flight_count = in_flight_count;
        self.active_bytes = active_bytes;
        Ok(())
    }
}
