//! Original recovery/replacement journal validation, shared by replay and current writers.

use domain::{ArtifactUploadRecord, ArtifactUploadRecovery};
use std::collections::BTreeMap;

/// Checks complete original lineage before admitting an intent or recording actual replacement proof.
pub fn validate_upload_recovery(
    uploads: &BTreeMap<String, ArtifactUploadRecord>,
    recoveries: &BTreeMap<String, ArtifactUploadRecovery>,
    record: &ArtifactUploadRecovery,
) -> Result<(), String> {
    record.validate()?;
    if uploads.get(&record.upload.upload_id) != Some(&record.upload) {
        return Err("recovery lost its exact original Prepared upload".into());
    }
    if let Some(previous) = recoveries.get(&record.command_id) {
        previous.validates_successor(record)?;
    } else if record.completion.is_some()
        || recoveries.len() >= 1024
        || uploads.contains_key(&record.replacement_admission.upload_id)
        || recoveries
            .values()
            .any(|r| r.replacement_admission.upload_id == record.replacement_admission.upload_id)
    {
        return Err(
            "recovery lacks its first intent or reuses replacement identity/capacity".into(),
        );
    }
    if let Some(done) = &record.completion
        && uploads.get(&done.replacement.upload_id) != Some(&done.replacement)
    {
        return Err(
            "recovery completion lacks normal original replacement Completed evidence".into(),
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{ArtifactUploadPhase, ArtifactUploadRecoveryCompletion};

    /// Constructs only a labelled metadata contract fixture; no process, payload or quorum is claimed.
    fn fixture() -> ArtifactUploadRecovery {
        let upload = ArtifactUploadRecord {
            upload_id: "original".into(),
            principal_id: "parser-uploader".into(),
            tenant_id: "parser-tenant".into(),
            original_certificate: "parser-loopback".into(),
            admitted_at_ms: 1,
            deadline_ms: 10000,
            phase: ArtifactUploadPhase::Prepared,
            content_digest: Some(format!("sha256:{}", "a".repeat(64))),
            byte_size: Some(17),
            prepared_at_ms: Some(2),
            completed_at_ms: None,
        };
        let replacement = ArtifactUploadRecord {
            upload_id: "independent-replacement".into(),
            phase: ArtifactUploadPhase::Started,
            content_digest: None,
            byte_size: None,
            prepared_at_ms: None,
            admitted_at_ms: 3,
            deadline_ms: 10003,
            ..upload.clone()
        };
        ArtifactUploadRecovery {
            schema: "roboguide.artifact-upload-recovery/v0.1".into(),
            command_id: "original-recovery".into(),
            budget_ms: 10000,
            reader_id: "explicit-parser-reader".into(),
            upload,
            source_owner_id: format!("artifact-store:{}", "b".repeat(64)),
            replacement_admission: replacement,
            destination_owner_id: format!("artifact-store:{}", "c".repeat(64)),
            destination_device: 1,
            destination_inode: 2,
            controller_process_json: "{}".into(),
            kernel_budget_json: "{}".into(),
            canonical_grant_json: "{}".into(),
            completion: None,
        }
    }

    /// Replacement evidence cannot skip original intent/normal completion or change original inputs.
    #[test]
    fn recovery_replay_preserves_original_intent_and_independent_upload_order() {
        let first = fixture();
        let mut uploads = BTreeMap::from([(first.upload.upload_id.clone(), first.upload.clone())]);
        let mut recoveries = BTreeMap::new();
        validate_upload_recovery(&uploads, &recoveries, &first).unwrap();
        let mut done = first.clone();
        let mut completed = first.replacement_admission.clone();
        completed.phase = ArtifactUploadPhase::Completed;
        completed.content_digest = first.upload.content_digest.clone();
        completed.byte_size = first.upload.byte_size;
        completed.prepared_at_ms = Some(4);
        completed.completed_at_ms = Some(5);
        done.completion = Some(ArtifactUploadRecoveryCompletion {
            replacement: completed.clone(),
            reader_receipt_json: "{}".into(),
            reader_authority_digest: "d".repeat(64),
        });
        assert!(validate_upload_recovery(&uploads, &recoveries, &done).is_err());
        recoveries.insert(first.command_id.clone(), first.clone());
        assert!(validate_upload_recovery(&uploads, &recoveries, &done).is_err());
        uploads.insert(completed.upload_id.clone(), completed);
        validate_upload_recovery(&uploads, &recoveries, &done).unwrap();
        let mut changed = first.clone();
        changed.budget_ms += 1;
        assert!(validate_upload_recovery(&uploads, &recoveries, &changed).is_err());
        changed = first.clone();
        changed.destination_owner_id = format!("artifact-store:{}", "e".repeat(64));
        assert!(validate_upload_recovery(&uploads, &recoveries, &changed).is_err());
        recoveries.insert(first.command_id.clone(), done.clone());
        assert!(validate_upload_recovery(&uploads, &recoveries, &first).is_err());
        validate_upload_recovery(&uploads, &recoveries, &done).unwrap();
        uploads.get_mut(&first.upload.upload_id).unwrap().tenant_id = "other".into();
        assert!(validate_upload_recovery(&uploads, &recoveries, &done).is_err());
    }

    /// New recovery evidence cannot hide under an older payload codec even with a compatible wrapper.
    #[test]
    fn recovery_event_requires_its_actual_codec() {
        use ports::EventSink;
        let directory = tempfile::tempdir().unwrap();
        let log = crate::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let payload = domain::EventPayload::ArtifactUploadRecoveryTransition {
            record: Box::new(fixture()),
        };
        log.clone().append(
            domain::TimestampMs::new(3),
            &domain::CorrelationId::new("parser-recovery").unwrap(),
            None,
            payload.clone(),
        );
        assert!(log.take_error().unwrap().is_none());
        let original = log.events().unwrap()[0].clone();
        assert_eq!(original.payload_schema, "domain.EventPayload.json/v32");
        let mut mislabeled = original;
        mislabeled.payload_schema = "domain.EventPayload.json/v31".into();
        assert!(crate::validate_persisted_event(&mislabeled).is_err());
    }
}
