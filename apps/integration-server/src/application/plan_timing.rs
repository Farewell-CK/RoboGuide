//! Opt-in dependency previews over exact plan bytes and optional caller-supplied history.

use domain::{MissionPlan, TaskId};
use orchestration::PlanTimingOffset;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json, value::RawValue};
use std::collections::{BTreeMap, BTreeSet};

/// The opt-in view wrapping one otherwise unmodified published-contract assessment.
pub(crate) const SCHEMA: &str = "roboguide.plan-timing-preview/v0.1";
/// Upper limit for one history scenario, independent of the size of the plan.
const MAX_ESTIMATES: usize = 256;
/// Upper limit for returned windows, paths and missing-evidence lists.
const MAX_ROWS: usize = 32;

/// One caller-supplied ready-to-satisfaction observation set for a logical Task.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct TimingEstimate {
    /// Task identity inside the exact previewed plan.
    task_id: String,
    /// Elapsed time including dispatch and the required completion evidence.
    duration_ms: u64,
    /// Count of observations represented by this one scenario value.
    sample_count: u32,
    /// Digest of the input artifact supplied by the caller.
    source_digest: String,
}

/// Opt-in envelope preserving the plan's original wire bytes for assessment.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HistoryRequest<'a> {
    /// Contract for the optional caller-supplied scenario.
    schema_version: &'a str,
    /// Nested plan decoded by the ordinary MissionPlan reader.
    plan: &'a RawValue,
    /// Estimates keyed by logical Task, with duplicate IDs rejected.
    estimates: Vec<TimingEstimate>,
}

/// A parsed request whose duration observations are used only for diagnostic projections.
#[derive(Default)]
pub(crate) struct TimingScenario {
    /// Report the original estimate values and their sources.
    estimates: Vec<TimingEstimate>,
    /// Key the corresponding durations by validated Task identity.
    durations: BTreeMap<TaskId, u64>,
}

impl TimingScenario {
    /// Retain legacy plan bodies and decode explicit versioned preview requests.
    pub(crate) fn read<'a>(
        body: &'a str,
        version: Option<&str>,
    ) -> Result<(&'a str, Option<Self>), String> {
        match version {
            None => Ok((body, None)),
            Some("v0.1") => Ok((body, Some(Self::default()))),
            Some("history-v0.1") => {
                let request: HistoryRequest<'_> = serde_json::from_str(body)
                    .map_err(|_| "malformed plan timing history request")?;
                if request.schema_version != "roboguide.plan-timing-history/v0.1"
                    || request.estimates.is_empty()
                    || request.estimates.len() > MAX_ESTIMATES
                {
                    return Err("unsupported or oversized plan timing history request".into());
                }
                let mut durations = BTreeMap::new();
                for estimate in &request.estimates {
                    let task_id = TaskId::new(&estimate.task_id).map_err(|e| e.to_string())?;
                    if estimate.sample_count == 0
                        || !valid_digest(&estimate.source_digest)
                        || durations.insert(task_id, estimate.duration_ms).is_some()
                    {
                        return Err(
                            "plan timing estimates need distinct Tasks and source evidence".into(),
                        );
                    }
                }
                Ok((
                    request.plan.get(),
                    Some(Self {
                        estimates: request.estimates,
                        durations,
                    }),
                ))
            }
            Some(_) => Err("unsupported plan timing preview version".into()),
        }
    }

    /// Bind every estimate to a Task in this already-decoded plan.
    pub(crate) fn check_tasks(&self, plan: &MissionPlan) -> Result<(), String> {
        let tasks = plan.task_graph();
        if self.durations.keys().any(|task| tasks.task(task).is_none()) {
            return Err("plan timing estimate names an unknown Task".into());
        }
        Ok(())
    }

    /// Supply the explicit scenario to the read-only dependency analysis.
    pub(crate) fn durations(&self) -> &BTreeMap<TaskId, u64> {
        &self.durations
    }

    /// Return bounded timing diagnostics and mark sampled estimates as advisory.
    pub(crate) fn preview(&self, plan: &MissionPlan, offsets: &[PlanTimingOffset]) -> Value {
        let tasks = plan.task_graph().tasks();
        let mut windows: Vec<_> = tasks
            .iter()
            .enumerate()
            .filter_map(|(index, task)| {
                let bounds = task.requirement().timing();
                let latest_start = bounds.latest_start_offset_ms();
                let deadline = bounds.completion_deadline_offset_ms();
                let ready_deadline = latest_start.into_iter().chain(deadline).min()?;
                let offset = offsets[index];
                let conflict = offset.declared_ready_ms > ready_deadline;
                let risk = offset
                    .estimated_ready_ms
                    .is_some_and(|ready| ready > ready_deadline)
                    || offset
                        .estimated_completion_ms
                        .zip(deadline)
                        .is_some_and(|(finish, deadline)| finish > deadline);
                let complete = offset.estimated_ready_ms.is_some()
                    && (deadline.is_none() || offset.estimated_completion_ms.is_some());
                // Present definite conflicts and estimated risks before uncertain rows.
                let (priority, status) = if conflict {
                    (0, "declared_conflict")
                } else if risk {
                    (1, "estimated_risk")
                } else if !complete {
                    (2, "insufficient_estimates")
                } else {
                    (3, "within_estimate")
                };
                Some((priority, index, status))
            })
            .collect();
        windows.sort_unstable_by_key(|(priority, index, _)| (*priority, *index));
        let truncated = windows.len() > MAX_ROWS;
        let rows: Vec<_> = windows
            .into_iter()
            .take(MAX_ROWS)
            .map(|(priority, index, status)| {
                let task = &tasks[index];
                let bounds = task.requirement().timing();
                let offset = offsets[index];
                let (path, path_truncated) = critical_path(plan, offsets, index, priority == 0);
                let (missing, missing_truncated) = self.missing_estimates(plan, index);
                json!({
                    "task_id": task.task_id(),
                    "status": status,
                    "declared_ready_lower_bound_ms": offset.declared_ready_ms,
                    "estimated_ready_ms": offset.estimated_ready_ms,
                    "estimated_completion_ms": offset.estimated_completion_ms,
                    "latest_start_offset_ms": bounds.latest_start_offset_ms(),
                    "completion_deadline_offset_ms": bounds.completion_deadline_offset_ms(),
                    "critical_path": path,
                    "critical_path_truncated": path_truncated,
                    "missing_estimate_task_ids": missing,
                    "missing_estimates_truncated": missing_truncated,
                })
            })
            .collect();
        json!({
            "anchor": "mission_acceptance",
            "estimate_scope": "caller_supplied_advisory",
            "estimates": self.estimates,
            "tasks": rows,
            "tasks_truncated": truncated,
        })
    }

    /// List missing observations on the dependency graph of one bounded Task.
    fn missing_estimates<'a>(
        &self,
        plan: &'a MissionPlan,
        index: usize,
    ) -> (Vec<&'a TaskId>, bool) {
        let graph = plan.task_graph();
        let task = &graph.tasks()[index];
        let mut visited = BTreeSet::new();
        let mut pending = vec![task.task_id()];
        let mut missing = Vec::new();
        while let Some(id) = pending.pop() {
            if !visited.insert(id) {
                continue;
            }
            if !self.durations.contains_key(id) {
                if missing.len() == MAX_ROWS {
                    return (missing, true);
                }
                missing.push(id);
            }
            pending.extend(graph.task(id).expect("validated dependency").dependencies());
        }
        (missing, false)
    }
}

/// Walk backward from the Task, preserving the causally relevant suffix when capped.
fn critical_path<'a>(
    plan: &'a MissionPlan,
    offsets: &[PlanTimingOffset],
    index: usize,
    declared: bool,
) -> (Vec<&'a TaskId>, bool) {
    let mut result = Vec::new();
    let mut next = Some(index);
    while let Some(index) = next {
        if result.len() == MAX_ROWS {
            break;
        }
        result.push(plan.task_graph().tasks()[index].task_id());
        next = if declared {
            offsets[index].declared_predecessor
        } else {
            offsets[index].estimated_predecessor
        };
    }
    result.reverse();
    (result, next.is_some())
}

/// Accept the bounded content digest used by other read-only planning evidence.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    })
}
