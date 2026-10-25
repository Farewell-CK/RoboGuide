//! Original SQL/reference rollback when the actual request witness fails at the last Commit gate.

use super::*;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// Explicit contract fixture whose source is stable but whose final request witness can be revoked.
struct CommitWitness {
    /// Actual original transaction inspected independently of rebuildable catalog caches.
    events: SqliteEventLog,
    /// Test-controlled permission at the final witness boundary, never a deployment grant.
    allowed: AtomicBool,
    /// Actual uncommitted event head seen immediately before the final permission decision.
    observed_head: AtomicU64,
}

impl MemoryProviderAdmission for CommitWitness {
    /// Rechecks the wrapped principal and can revoke only after the actual candidate events are saved.
    fn revalidate_artifact_principal(&self, _principal: &ArtifactPrincipal) -> Result<(), String> {
        if !self.allowed.load(Ordering::SeqCst)
            && self
                .events
                .current_batch_event_sequence()
                .is_ok_and(|sequence| sequence >= 3)
        {
            return Err("original actual wrapped witness revoked before Commit".into());
        }
        Ok(())
    }
    /// Rejects after candidate/reference/checkpoint saves so all three must roll back together.
    fn revalidate_artifact_request(&self) -> Result<(), String> {
        self.observed_head.store(
            self.events.current_batch_event_sequence().unwrap(),
            Ordering::SeqCst,
        );
        if self.allowed.load(Ordering::SeqCst) {
            Ok(())
        } else {
            Err("original request witness revoked before Commit".into())
        }
    }

    /// Supplies explicitly synthetic original attribution only for this isolated storage contract.
    fn publication_source(&self) -> Result<ArtifactPublicationSource, String> {
        Ok(ArtifactPublicationSource {
            tenant_id: "fixture-tenant".into(),
            principal_id: "fixture-principal".into(),
            authority_digest: format!("sha256:{}", "a".repeat(64)),
        })
    }

    /// Delegates fixture semantic admission; production registration has independent tests.
    fn admit_manifest(&self, manifest: &MemoryArtifactManifest) -> Result<(), String> {
        AllowTestMemoryAdmission.admit_manifest(manifest)
    }

    /// Delegates fixture replica validation without creating a deployment replica.
    fn admit_replica(
        &self,
        node: &NodeId,
        provider: &str,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        AllowTestMemoryAdmission.admit_replica(node, provider, manifest)
    }

    /// Delegates fixture publisher validation without registering a Node.
    fn admit_publisher(
        &self,
        publisher: Option<&MemoryPublicationIdentity>,
        node: &NodeId,
    ) -> Result<(), String> {
        AllowTestMemoryAdmission.admit_publisher(publisher, node)
    }

    /// Delegates unused localization admission; no physical or device fact is produced here.
    fn admit_localization_evidence(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        AllowTestMemoryAdmission.admit_localization_evidence(evidence)
    }
}

/// Revocation after hashing/candidate save commits neither publication nor reference and permits no cache leak.
#[test]
fn final_request_witness_rolls_back_publication_and_original_reference() {
    let directory = tempfile::tempdir().unwrap();
    let store = FileSystemArtifactStore::new_with_retention(directory.path().join("cas")).unwrap();
    let bytes = b"actual fixture bytes for final witness rollback";
    let digest = digest_bytes(bytes);
    let mut upload = store.begin_upload("fixture-witness").unwrap();
    upload.write_chunk(bytes).unwrap();
    upload.finalize(&digest, bytes.len() as u64).unwrap();
    let events = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    seed_controller_checkpoint(&events);
    let gate = Arc::new(crate::application::ApplicationWriter::default());
    let catalog = ArtifactCatalog::replay_with_store(&events, gate.clone(), Some(store)).unwrap();
    let manifest =
        fixture_memory_manifest("witness", MemoryKind::Artifact, &digest, bytes.len() as u64);
    let payload = EventPayload::MemoryManifestPublished {
        manifest: manifest.clone(),
    };
    let witness = CommitWitness {
        events: events.clone(),
        allowed: AtomicBool::new(false),
        observed_head: AtomicU64::new(0),
    };
    let _guard = gate.lock().unwrap();
    assert_eq!(
        catalog
            .append_authorized_under_writer(payload.clone(), &witness)
            .unwrap_err()
            .status(),
        "403 Forbidden"
    );
    assert_eq!(witness.observed_head.load(Ordering::SeqCst), 2);
    assert_eq!(events.len().unwrap(), 0);
    assert!(
        events
            .artifact_catalog_reference(manifest.selector())
            .unwrap()
            .is_none()
    );
    assert!(catalog.memories().unwrap().is_empty());
    assert!(
        catalog
            .retention
            .lock()
            .unwrap()
            .record(manifest.selector())
            .is_none()
    );
    assert_eq!(
        events.load_checkpoint().unwrap().unwrap().checkpoint_json,
        TEST_CHECKPOINT_JSON
    );
    witness.allowed.store(true, Ordering::SeqCst);
    catalog
        .append_authorized_under_writer(payload, &witness)
        .unwrap();
    assert_eq!(events.len().unwrap(), 2);
    assert!(catalog.memory(manifest.selector()).unwrap().is_some());
    assert!(
        events
            .artifact_catalog_reference(manifest.selector())
            .unwrap()
            .is_some()
    );
}

/// Explicit legacy adoption must retain raw old bytes and roll back reference/receipt on last witness loss.
#[test]
fn legacy_adoption_preserves_original_publication_and_requires_same_transaction_receipt() {
    let directory = tempfile::tempdir().unwrap();
    let store = FileSystemArtifactStore::new_with_retention(directory.path().join("cas")).unwrap();
    let bytes = b"actual unit SQL/CAS legacy publication";
    let digest = digest_bytes(bytes);
    let mut upload = store.begin_upload("unit-legacy-adoption").unwrap();
    upload.write_chunk(bytes).unwrap();
    upload.finalize(&digest, bytes.len() as u64).unwrap();
    let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    seed_controller_checkpoint(&log);
    let gate = Arc::new(crate::application::ApplicationWriter::default());
    let manifest = fixture_memory_manifest(
        "unit-legacy-adoption",
        MemoryKind::Artifact,
        &digest,
        bytes.len() as u64,
    );
    let old = ArtifactCatalog::replay_with_gate(&log, gate.clone()).unwrap();
    old.append(EventPayload::MemoryManifestPublished {
        manifest: manifest.clone(),
    })
    .unwrap();
    let original = log.events().unwrap()[0].clone();
    drop(old);
    let catalog = ArtifactCatalog::replay_with_store(&log, gate, Some(store.clone())).unwrap();
    let witness = CommitWitness {
        events: log.clone(),
        allowed: AtomicBool::new(false),
        observed_head: AtomicU64::new(0),
    };
    let principal = ArtifactPrincipal::local();
    let admission = super::super::authenticated::AuthenticatedAdmission {
        authority: &witness,
        principal: &principal,
        catalog: &catalog,
        import_id: None,
    };
    let path = "/v1/artifact-references/unit-legacy-adoption/r1";
    let observation =
        super::super::adoption::observe(&catalog, &admission, &format!("{path}/legacy")).unwrap();
    let Response::Json(_, body) = observation else {
        panic!("legacy observation must be JSON")
    };
    let request = Request { path:format!("{path}/adopt"),body:serde_json::to_vec(&serde_json::json!({"schema":"roboguide.legacy-catalog-adoption-command/v0.1","command_id":"unit-current-adoption","original_publication_digest":body["original_publication_digest"]})).unwrap(), memory_publisher:None };
    assert_eq!(
        super::super::adoption::adopt(&catalog, &admission, &request, &request.path)
            .err()
            .unwrap()
            .status(),
        "403 Forbidden"
    );
    assert_eq!(log.len().unwrap(), 1);
    assert!(
        catalog
            .retention
            .lock()
            .unwrap()
            .record(manifest.selector())
            .is_none()
    );
    witness.allowed.store(true, Ordering::SeqCst);
    super::super::adoption::adopt(&catalog, &admission, &request, &request.path).unwrap();
    assert_eq!(log.len().unwrap(), 3);
    let first = catalog
        .retention
        .lock()
        .unwrap()
        .adoption(manifest.selector())
        .unwrap()
        .clone();
    assert_eq!(first.publication.payload_json, original.payload_json);
    assert!(first.historical_tenant.is_none() && first.historical_publisher.is_none());
    super::super::adoption::adopt(&catalog, &admission, &request, &request.path).unwrap();
    assert_eq!(log.len().unwrap(), 3);
    let replay = ArtifactCatalog::replay_with_store(
        &log,
        Arc::new(crate::application::ApplicationWriter::default()),
        Some(store.clone()),
    )
    .unwrap();
    assert_eq!(
        replay
            .retention
            .lock()
            .unwrap()
            .adoption(manifest.selector()),
        Some(&first)
    );
    let connection = rusqlite::Connection::open(directory.path().join("events.sqlite3")).unwrap();
    connection
        .execute(
            "DELETE FROM events WHERE payload_schema='domain.EventPayload.json/v30'",
            [],
        )
        .unwrap();
    assert!(
        ArtifactCatalog::replay_with_store(
            &log,
            Arc::new(crate::application::ApplicationWriter::default()),
            Some(store)
        )
        .is_err()
    );
}
