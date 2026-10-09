//! Read-only operation progress with attempt ownership and receive-time freshness.

use super::*;
use domain::{
    OperationRef, StateObjectClass, StateRecord, StateRecordKey, StateSemantic, TimestampMs,
};

/// Payload schema carried by an explicitly configured fixed Node State export.
pub const EXECUTION_PROGRESS_SCHEMA: &str = "roboguide.execution-progress/v0.1";

/// Direct local activity, independent of heartbeat, displacement and Task satisfaction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OperationActivity {
    /// Work is being attempted under the local operation contract.
    Working,
    /// Intentional wait; an unchanged counter cannot classify it as stalled.
    Waiting,
    /// The local owner explicitly reports inability to advance.
    Blocked,
    /// Reliable operation progress is unavailable.
    Unavailable,
}

/// One local sample, never an execution outcome or authorization to repeat an action.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperationProgressSample {
    /// Exact physical attempt identity received by the local operation.
    pub execution_id: String,
    /// Exact canonical operation defining this sample's stages and units.
    pub operation: OperationRef,
    /// Monotonic stage generation; a counter reset requires a later stage.
    pub stage_epoch: u64,
    /// Monotonic units within a stage; absent when no meaningful measure exists.
    pub completed_units: Option<u64>,
    /// Direct local activity observation.
    pub activity: OperationActivity,
}

/// Bounded batch collected by an existing read-only State workflow.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperationProgressBatch {
    /// Exact version, checked before samples are admitted.
    pub schema_version: String,
    /// At most 128 unique attempt observations within the existing State byte bound.
    pub executions: Vec<OperationProgressSample>,
}

/// Retained source order and original receive times; checkpoint restore never renews them.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ProgressObservation {
    /// Latest admitted sample.
    sample: OperationProgressSample,
    /// Attributed State record carrying source, epoch, sequence and validity.
    record: ProgressSource,
    /// Local receive time of the latest advance or observation baseline.
    last_advanced_at: TimestampMs,
}

/// Bounded attribution of a State sample without copying its whole multi-execution payload.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct ProgressSource {
    /// Exact registered object, channel and LocalSystem ownership.
    key: StateRecordKey,
    /// Original source generation, independent of the source timestamp.
    source_epoch: Option<String>,
    /// Original sequence inside this source generation.
    sequence: u64,
    /// Controller receive time, preserved across checkpoint restore.
    received_at: TimestampMs,
    /// Configured validity, without any restart renewal.
    valid_for_ms: u64,
}

impl ProgressSource {
    /// Retains attribution and freshness; raw State payload remains in its State authority.
    fn from_record(record: &StateRecord) -> Self {
        Self {
            key: record.key().clone(),
            source_epoch: record.source_epoch().map(str::to_owned),
            sequence: record.sequence(),
            received_at: record.received_at(),
            valid_for_ms: record.valid_for_ms(),
        }
    }
    /// Returns immutable object/channel/source identity.
    const fn key(&self) -> &StateRecordKey {
        &self.key
    }
    /// Returns source generation without comparing independent clocks.
    fn source_epoch(&self) -> Option<&str> {
        self.source_epoch.as_deref()
    }
    /// Returns original ordering sequence.
    const fn sequence(&self) -> u64 {
        self.sequence
    }
    /// Returns original Controller receive time.
    const fn received_at(&self) -> TimestampMs {
        self.received_at
    }
    /// Tests configured receive-relative expiry without extending the original TTL.
    fn is_stale_at(&self, now: TimestampMs) -> bool {
        now.as_millis()
            >= self
                .received_at
                .as_millis()
                .saturating_add(self.valid_for_ms)
    }
}

/// Read-only interpretation, never automatic cancellation or replacement policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum ProgressDisposition {
    /// Missing, stale, unavailable or unconfirmed current-attempt evidence.
    Unknown,
    /// Local operation is working.
    Working,
    /// Intentional local wait.
    Waiting,
    /// Direct local inability to advance.
    Blocked,
    /// Measured work has not advanced within an explicitly supplied policy interval.
    Stalled,
    /// Runtime has immutable terminal evidence.
    Terminal,
}

impl RuntimeExecutionManager {
    /// Admits current-attempt progress without changing health, lifecycle or command routing.
    pub fn observe_progress(
        &mut self,
        record: &StateRecord,
        sample: OperationProgressSample,
    ) -> bool {
        let Some(context) = self.executions.get(&sample.execution_id) else {
            return false;
        };
        let command = &context.command;
        if sample.execution_id.len() > 256
            || record.payload_schema() != EXECUTION_PROGRESS_SCHEMA
            || record.key().semantic() != StateSemantic::Reported
            || record.key().object().class() != StateObjectClass::Node
            || record.key().object().object_id() != command.node_id().as_str()
            || record.key().source().node_id() != Some(command.node_id())
            || &sample.operation != command.intent().operation()
            || self.current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
                != Some(sample.execution_id.as_str())
            || self
                .execution_status(&sample.execution_id)
                .is_some_and(ExecutionStatus::is_terminal)
            || record.valid_for_ms() == 0
        {
            return false;
        }
        let mut last_advanced_at = record.received_at();
        if let Some(previous) = self.progress.get(&sample.execution_id) {
            if previous.record.key() != record.key() {
                return false;
            }
            if previous.record.source_epoch() == record.source_epoch() {
                if record.sequence() <= previous.record.sequence()
                    || record.received_at() < previous.record.received_at()
                    || sample.stage_epoch < previous.sample.stage_epoch
                    || (sample.stage_epoch == previous.sample.stage_epoch
                        && sample
                            .completed_units
                            .zip(previous.sample.completed_units)
                            .is_some_and(|(next, old)| next < old))
                {
                    return false;
                }
                if sample.stage_epoch == previous.sample.stage_epoch
                    && sample.completed_units == previous.sample.completed_units
                    && sample.activity == previous.sample.activity
                    && !previous.record.is_stale_at(record.received_at())
                {
                    last_advanced_at = previous.last_advanced_at;
                }
            }
        }
        self.progress.insert(
            sample.execution_id.clone(),
            ProgressObservation {
                sample,
                record: ProgressSource::from_record(record),
                last_advanced_at,
            },
        );
        true
    }

    /// Interprets progress using receive time and an optional operation-specific stall interval.
    pub fn progress_disposition(
        &self,
        execution_id: &str,
        now: TimestampMs,
        stall_after_ms: Option<u64>,
    ) -> ProgressDisposition {
        match self.execution_status(execution_id) {
            Some(status) if status.is_terminal() => return ProgressDisposition::Terminal,
            Some(ExecutionStatus::Running | ExecutionStatus::Accepted) => {}
            _ => return ProgressDisposition::Unknown,
        }
        let Some(observation) = self.progress.get(execution_id) else {
            return ProgressDisposition::Unknown;
        };
        let command = &self.executions[execution_id].command;
        if self.current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
            != Some(execution_id)
            || observation.record.is_stale_at(now)
            || now < observation.record.received_at()
        {
            return ProgressDisposition::Unknown;
        }
        match observation.sample.activity {
            OperationActivity::Unavailable => ProgressDisposition::Unknown,
            OperationActivity::Waiting => ProgressDisposition::Waiting,
            OperationActivity::Blocked => ProgressDisposition::Blocked,
            OperationActivity::Working
                if observation.sample.completed_units.is_some()
                    && stall_after_ms.is_some_and(|budget| {
                        budget > 0
                            && now
                                .as_millis()
                                .saturating_sub(observation.last_advanced_at.as_millis())
                                >= budget
                    }) =>
            {
                ProgressDisposition::Stalled
            }
            OperationActivity::Working => ProgressDisposition::Working,
        }
    }

    /// Fences samples when registration changes; old source contracts cannot renew new owners.
    pub fn fence_progress_for_node(&mut self, node_id: &NodeId) {
        self.progress
            .retain(|_, observation| observation.record.key().source().node_id() != Some(node_id));
    }

    /// Returns the retained raw sample; callers still need the independent freshness classification.
    pub fn progress_sample(&self, execution_id: &str) -> Option<&OperationProgressSample> {
        self.progress
            .get(execution_id)
            .map(|observation| &observation.sample)
    }

    /// Returns immutable attribution, original times and sample for diagnostic evidence.
    pub fn progress_evidence(&self, execution_id: &str) -> Option<&ProgressObservation> {
        self.progress.get(execution_id)
    }

    /// Checks retained identities and receive times before restored progress becomes queryable.
    pub(super) fn validate_progress(&self) -> Result<(), ExecutionRuntimeError> {
        for (id, observation) in &self.progress {
            let Some(context) = self.executions.get(id) else {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "progress has no execution".into(),
                ));
            };
            if id != &observation.sample.execution_id
                || observation.record.key().semantic() != StateSemantic::Reported
                || observation.record.key().object().class() != StateObjectClass::Node
                || observation.record.key().object().object_id()
                    != context.command.node_id().as_str()
                || observation.record.valid_for_ms == 0
                || observation.record.key().source().node_id() != Some(context.command.node_id())
                || &observation.sample.operation != context.command.intent().operation()
                || observation.last_advanced_at > observation.record.received_at()
            {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "progress identity or receive time is invalid".into(),
                ));
            }
        }
        Ok(())
    }
}
