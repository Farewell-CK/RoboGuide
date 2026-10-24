#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! State & Memory Plane facade for implemented observable projections.

/// Current composition authority wrapper, shared by Controller, replica supervisor and storage fences.
pub const CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v55";

/// Returns whether metadata-only inactive installation would lose mandatory original recovery authority.
pub fn checkpoint_requires_complete_recovery(schema: &str) -> bool {
    matches!(
        schema,
        CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
            | "roboguide.controller-checkpoint/v49"
            | "roboguide.controller-checkpoint/v50"
            | "roboguide.controller-checkpoint/v51"
            | "roboguide.controller-checkpoint/v52"
            | "roboguide.controller-checkpoint/v53"
            | "roboguide.controller-checkpoint/v54"
    )
}

/// Returns whether a supported Controller wrapper requires an exact durable command journal head.
pub fn checkpoint_requires_authority_journal(schema: &str) -> bool {
    matches!(
        schema,
        "roboguide.controller-checkpoint/v27"
            | "roboguide.controller-checkpoint/v28"
            | "roboguide.controller-checkpoint/v29"
            | "roboguide.controller-checkpoint/v30"
            | "roboguide.controller-checkpoint/v31"
            | "roboguide.controller-checkpoint/v32"
            | "roboguide.controller-checkpoint/v33"
            | "roboguide.controller-checkpoint/v34"
            | "roboguide.controller-checkpoint/v35"
            | "roboguide.controller-checkpoint/v36"
            | "roboguide.controller-checkpoint/v37"
            | "roboguide.controller-checkpoint/v38"
            | "roboguide.controller-checkpoint/v39"
            | "roboguide.controller-checkpoint/v40"
            | "roboguide.controller-checkpoint/v41"
            | "roboguide.controller-checkpoint/v42"
            | "roboguide.controller-checkpoint/v43"
            | "roboguide.controller-checkpoint/v44"
            | "roboguide.controller-checkpoint/v45"
            | "roboguide.controller-checkpoint/v46"
            | "roboguide.controller-checkpoint/v47"
            | "roboguide.controller-checkpoint/v48"
            | "roboguide.controller-checkpoint/v49"
            | "roboguide.controller-checkpoint/v50"
            | "roboguide.controller-checkpoint/v51"
            | "roboguide.controller-checkpoint/v52"
            | "roboguide.controller-checkpoint/v53"
            | "roboguide.controller-checkpoint/v54"
            | CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
    )
}

mod allocation;
mod artifact_retention;
mod upload_recovery;
mod upload_retention;
pub use artifact_retention::ArtifactRetentionProjection;
pub use upload_recovery::validate_upload_recovery;
pub use upload_retention::validate_upload_retirement;
#[cfg(test)]
mod artifact_retention_tests;
mod belief;
mod covariance_intersection;
mod decision_policy;
mod decision_state;
mod event_log;
pub use event_log::ControllerStorageFormat;
mod memory;
mod node;
mod spatial_memory;
mod state_record;
mod task_satisfaction;

pub use allocation::InMemoryAllocationState;
pub use belief::derive_belief;
pub use covariance_intersection::{
    CovarianceIntersectionResult, GaussianEstimate, covariance_intersection,
};
pub use decision_policy::{
    ConflictReducer, DECISION_EVIDENCE_POLICY_SCHEMA_V0_1, DecisionEvidencePolicy,
    DecisionEvidencePolicyError, DecisionKind, EvidenceDisposition, EvidenceEvaluation,
};
pub use decision_state::DecisionStateSnapshot;
pub use event_log::{
    APPLICATION_COMMIT_SCHEMA, APPLICATION_CONTENT_INITIALIZATION_SCHEMA,
    ApplicationBatchReplicator, ApplicationCommitBatch, ArchiveAbortIntent, ArchiveAbortReceipt,
    ArchiveAbortRequest, ArchiveAbortSink, ArchiveContent, ArchiveCopierFence, ArchiveIntent,
    ArchiveLeaseIdentity, ArchiveManifest, ArchiveRequest, ArchiveSink, AuthorityWriteStatus,
    CheckpointStoragePolicy, CheckpointWriteStats, CompleteReplicaState,
    ControlCommandJournalEntry, FileArchiveSink, MAX_ARCHIVE_BYTES, MAX_ARCHIVE_EVENTS,
    PersistedCheckpoint, PersistedEvent, REPLICA_RECOVERY_PROJECTION_SCHEMA, ReplicaArchiveAbort,
    ReplicaArchiveManifest, ReplicaArchiveOperation, ReplicaRecoveryProjection, SqliteEventLog,
    SqliteEventLogError, WriteFailure, WriteRecoveryCommand, WriteRecoveryReceipt,
    checkpoint_identity, validate_persisted_event,
};
pub use memory::MemoryCatalogProjection;
pub use node::InMemorySharedNodeState;
pub use spatial_memory::MapCatalogProjection;
pub use state_record::StateRecordProjection;
pub use task_satisfaction::InMemoryTaskSatisfactionState;
