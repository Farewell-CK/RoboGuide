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
    let gate = Arc::new(Mutex::new(()));
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
