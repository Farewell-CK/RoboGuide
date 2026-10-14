#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! State & Memory Plane facade for implemented observable projections.

/// Current composition authority wrapper, shared by Controller, replica supervisor and storage fences.
pub const CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v28";

/// Returns whether a supported Controller wrapper requires an exact durable command journal head.
pub fn checkpoint_requires_authority_journal(schema: &str) -> bool {
    matches!(
        schema,
        "roboguide.controller-checkpoint/v27" | CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
    )
}

mod allocation;
mod decision_policy;
mod event_log;
mod memory;
mod node;
mod spatial_memory;
mod state_record;
mod task_satisfaction;

pub use allocation::InMemoryAllocationState;
pub use decision_policy::{
    ConflictReducer, DECISION_EVIDENCE_POLICY_SCHEMA_V0_1, DecisionEvidencePolicy,
    DecisionEvidencePolicyError, DecisionKind, EvidenceDisposition, EvidenceEvaluation,
};
pub use event_log::{
    APPLICATION_COMMIT_SCHEMA, ApplicationBatchReplicator, ApplicationCommitBatch, ArchiveManifest,
    ArchiveSink, CheckpointStoragePolicy, CheckpointWriteStats, ControlCommandJournalEntry,
    FileArchiveSink, MAX_ARCHIVE_BYTES, MAX_ARCHIVE_EVENTS, PersistedCheckpoint, PersistedEvent,
    SqliteEventLog, SqliteEventLogError, checkpoint_identity, validate_persisted_event,
};
pub use memory::MemoryCatalogProjection;
pub use node::InMemorySharedNodeState;
pub use spatial_memory::MapCatalogProjection;
pub use state_record::StateRecordProjection;
pub use task_satisfaction::InMemoryTaskSatisfactionState;
