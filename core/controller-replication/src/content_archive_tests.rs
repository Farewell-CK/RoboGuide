//! Real SQL/CAS metadata fixtures; direct DTO checks prove no election, quorum, Node or device fact.

use super::*;
use sha2::{Digest, Sha256};

/// Captures one accepted original complete source without claiming a live network acknowledgement.
fn view(
    candidate: ContentApplicationBatch,
    revision: u64,
    events: Vec<state::PersistedEvent>,
    commands: Vec<state::ControlCommandJournalEntry>,
) -> ReplicaView {
    ReplicaView {
        revision,
        checkpoint: Some(candidate.batch.checkpoint.clone()),
        events,
        commands,
        history_anchor: None,
        content: Some(candidate.accepted_state().unwrap()),
        application_upgrade: None,
    }
}

/// Original complete data, package and pending record are independently retained for tamper checks.
fn admission(
    previous: &ReplicaView,
) -> (state::CompleteReplicaState, state::CoordinatedArchiveRecord) {
    let content = previous.content.as_ref().unwrap();
    let original = state::CompleteReplicaState {
        schema: "roboguide.complete-replica-state/v0.1".into(),
        checkpoint: previous.checkpoint.clone().unwrap(),
        events: previous.events.clone(),
        commands: previous.commands.clone(),
        recovery: content.recovery.clone(),
    };
    let package = state::coordinated_archive_package(
        &original,
        original.checkpoint.event_sequence,
        content
            .inputs
            .iter()
            .map(|k| state::ArchiveContentKey {
                source_owner: k.source_owner.clone(),
                content_digest: k.content_digest.clone(),
                byte_size: k.byte_size,
            })
            .collect(),
    )
    .unwrap();
    let bytes = state::coordinated_archive_bytes(&package).unwrap();
    let package_size = bytes.len() as u64;
    let row = state::CoordinatedArchiveRecord {
        intent: state::CoordinatedArchiveIntent {
            schema: "roboguide.coordinated-archive-intent/v0.1".into(),
            request: state::CoordinatedArchiveRequest {
                command_id: "original-contract-archive".into(),
                principal: "actual-contract-admin".into(),
                source_owner: content.source_writer.owner.clone(),
                end_sequence: package.manifest.end_sequence,
                budget_ms: 1000,
            },
            authority_id: package.authority_id.clone(),
            issuing_epoch: format!(
                "{}:{}",
                content.profile_identity,
                serde_json::to_string(&content.epoch).unwrap()
            ),
            previous_floor: 0,
            manifest: package.manifest.clone(),
            package: state::ArchiveContentKey {
                source_owner: content.source_writer.owner.clone(),
                content_digest: format!("sha256:{:x}", Sha256::digest(bytes)),
                byte_size: package_size,
            },
            catalog_digest: format!(
                "sha256:{:x}",
                Sha256::digest(state::coordinated_archive_bytes(&package.catalog).unwrap())
            ),
            recovery_digest: package.recovery_digest,
            boot_id: "0".repeat(36),
            time_namespace: "time:[original-contract-only]".into(),
            issued_ms: 10,
            deadline_ms: 1010,
        },
        completed_ms: None,
        abort: None,
    };
    (original, row)
}

/// Creates the actual metadata proposal while preserving the original complete semantic checkpoint.
fn proposal(
    previous: &ReplicaView,
    cas: &artifact_store::FileSystemArtifactStore,
    recovery: state::ReplicaRecoveryProjection,
) -> Result<ContentApplicationBatch, String> {
    ContentApplicationBatch::from_source(
        &previous.content.as_ref().unwrap().profile_identity,
        previous.content.as_ref().unwrap().epoch,
        cas,
        previous,
        state::ApplicationCommitBatch {
            schema: state::APPLICATION_COMMIT_SCHEMA.into(),
            previous_event_sequence: previous.checkpoint.as_ref().unwrap().event_sequence,
            previous_checkpoint_digest: previous
                .checkpoint
                .as_ref()
                .map(state::checkpoint_identity),
            checkpoint: previous.checkpoint.clone().unwrap(),
            events: Vec::new(),
            commands: Vec::new(),
        },
        recovery,
    )
}

/// Prefix deletion requires prior immutable admission; exact protected history and original pins survive.
#[test]
fn actual_original_prefix_requires_admission_and_rejects_forged_package_or_floor() {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, _key) = crate::content_application_tests::original(directory.path());
    let initial = crate::content_application_tests::candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &"a".repeat(64),
    );
    let previous = view(
        initial.clone(),
        1,
        initial.batch.events.clone(),
        initial.batch.commands.clone(),
    );
    let (original, mut row) = admission(&previous);
    let pending = state::coordinated_archive_projection(&original, &row).unwrap();
    let candidate = proposal(&previous, &cas, pending.clone()).unwrap();
    assert_eq!(candidate.schema, CONTENT_ARCHIVE_APPLICATION_SCHEMA);
    assert_eq!(
        candidate.archive_effects(&previous).unwrap(),
        vec![row.intent.clone()]
    );
    let mut forged = candidate.clone();
    forged.recovery.coordinated_archives.as_mut().unwrap()[0]
        .intent
        .package
        .byte_size += 1;
    assert!(forged.validate_against(&previous, &"a".repeat(64)).is_err());
    let mut legacy = candidate.clone();
    legacy.schema = CONTENT_APPLICATION_SCHEMA.into();
    assert!(legacy.validate_against(&previous, &"a".repeat(64)).is_err());
    row.completed_ms = Some(20);
    let early = state::coordinated_archive_projection(&original, &row).unwrap();
    assert!(proposal(&previous, &cas, early).is_err());
    let admitted = view(
        candidate,
        2,
        previous.events.clone(),
        previous.commands.clone(),
    );
    let mut admitted_original = original;
    admitted_original.recovery = pending;
    let complete = state::coordinated_archive_projection(&admitted_original, &row).unwrap();
    let final_candidate = proposal(&admitted, &cas, complete.clone()).unwrap();
    assert!(
        final_candidate
            .inputs
            .iter()
            .any(|k| k.content_digest == row.intent.package.content_digest)
    );
    assert_eq!(
        final_candidate.origins,
        previous.content.as_ref().unwrap().origins
    );
    let archived = view(final_candidate, 3, Vec::new(), previous.commands.clone());
    archived
        .validate(
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            &"a".repeat(64),
        )
        .unwrap();
    let continued = proposal(&archived, &cas, complete).unwrap();
    continued
        .validate_against(&archived, &"a".repeat(64))
        .unwrap();
    assert!(continued.archive_effects(&archived).unwrap().is_empty());
    let mut wrong_epoch = admitted.clone();
    wrong_epoch.content.as_mut().unwrap().candidate.epoch.term += 1;
    assert!(
        proposal(
            &wrong_epoch,
            &cas,
            state::coordinated_archive_projection(&admitted_original, &row).unwrap()
        )
        .is_err()
    );
}
