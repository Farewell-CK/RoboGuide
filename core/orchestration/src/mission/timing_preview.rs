//! Read-only analysis of Mission-relative dependency windows.

use super::super::*;
use std::collections::VecDeque;

/// Earliest feasible offsets implied by declarations and an optional history scenario.
#[derive(Clone, Copy, Debug, Default)]
pub struct PlanTimingOffset {
    /// A lower bound from the Task's and its ancestors' declared earliest starts.
    pub declared_ready_ms: u64,
    /// The ancestor responsible for the declared lower bound, by Task index.
    pub declared_predecessor: Option<usize>,
    /// Earliest start in the supplied scenario, absent for an incomplete predecessor chain.
    pub estimated_ready_ms: Option<u64>,
    /// Scenario completion with dispatch/verification included in the supplied duration.
    pub estimated_completion_ms: Option<u64>,
    /// The predecessor on the longest estimated path, by Task index.
    pub estimated_predecessor: Option<usize>,
}

/// One contradiction provable without any operation, history, or resource estimate.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlanTimingConflict {
    /// Index of the Task in the accepted plan's wire order.
    pub task_index: usize,
    /// Exact timing field whose hard bound cannot be met.
    pub field: &'static str,
    /// The inconsistent declaration in milliseconds after Mission acceptance.
    pub bound_ms: u64,
    /// Earliest possible start from the declared dependency chain.
    pub declared_ready_ms: u64,
}

impl PlanTimingConflict {
    /// Identify the exact invalid declared window in the canonical input.
    pub fn path(self) -> String {
        format!("/tasks/{}/timing/{}", self.task_index, self.field)
    }

    /// Explain a contradiction that is independent of measured operation durations.
    pub fn message(self, plan: &MissionPlan) -> String {
        let task = &plan.task_graph().tasks()[self.task_index];
        format!(
            "Task {} cannot become ready before {} ms after Mission acceptance, but {} is {} ms",
            task.task_id(),
            self.declared_ready_ms,
            self.field,
            self.bound_ms
        )
    }
}

/// Project Task offsets in wire order using the already validated, acyclic plan graph.
///
/// Hard lower bounds take all ancestors' declared earliest starts into account.
/// A scenario uses only caller-supplied ready-to-satisfaction durations. Incomplete
/// or unrepresentable estimates yield an unavailable projection rather than zero.
pub fn project_plan_timing(
    plan: &MissionPlan,
    durations_ms: &BTreeMap<TaskId, u64>,
) -> Vec<PlanTimingOffset> {
    let tasks = plan.task_graph().tasks();
    let indices = tasks
        .iter()
        .enumerate()
        .map(|(index, task)| (task.task_id(), index))
        .collect::<BTreeMap<_, _>>();
    let mut successors = vec![Vec::new(); tasks.len()];
    let mut unsatisfied = Vec::with_capacity(tasks.len());
    let mut pending = VecDeque::new();
    for (index, task) in tasks.iter().enumerate() {
        unsatisfied.push(task.dependencies().len());
        if task.dependencies().is_empty() {
            pending.push_back(index);
        }
        for dependency in task.dependencies() {
            successors[indices[dependency]].push(index);
        }
    }
    let mut offsets = vec![PlanTimingOffset::default(); tasks.len()];
    while let Some(index) = pending.pop_front() {
        let task = &tasks[index];
        let earliest = task.requirement().timing().earliest_start_offset_ms();
        let mut row = PlanTimingOffset {
            declared_ready_ms: earliest,
            estimated_ready_ms: Some(earliest),
            ..Default::default()
        };
        for dependency in task.dependencies() {
            let parent = indices[dependency];
            let predecessor = offsets[parent];
            if predecessor.declared_ready_ms > row.declared_ready_ms {
                row.declared_ready_ms = predecessor.declared_ready_ms;
                row.declared_predecessor = Some(parent);
            }
            match (row.estimated_ready_ms, predecessor.estimated_completion_ms) {
                (Some(ready), Some(completion)) => {
                    if completion > ready
                        || (completion == ready && row.estimated_predecessor.is_none())
                    {
                        row.estimated_ready_ms = Some(completion);
                        row.estimated_predecessor = Some(parent);
                    }
                }
                _ => {
                    row.estimated_ready_ms = None;
                    row.estimated_predecessor = None;
                }
            }
        }
        row.estimated_completion_ms = row
            .estimated_ready_ms
            .zip(durations_ms.get(task.task_id()))
            .and_then(|(ready, duration)| ready.checked_add(*duration));
        offsets[index] = row;
        for successor in &successors[index] {
            unsatisfied[*successor] -= 1;
            if unsatisfied[*successor] == 0 {
                pending.push_back(*successor);
            }
        }
    }
    offsets
}

/// Iterate declaration-only contradictions against the same inclusive runtime bounds.
pub fn plan_timing_conflicts<'a>(
    plan: &'a MissionPlan,
    offsets: &'a [PlanTimingOffset],
) -> impl Iterator<Item = PlanTimingConflict> + 'a {
    plan.task_graph()
        .tasks()
        .iter()
        .zip(offsets)
        .enumerate()
        .flat_map(|(task_index, (task, offset))| {
            let timing = task.requirement().timing();
            [
                ("latest_start_offset_ms", timing.latest_start_offset_ms()),
                (
                    "completion_deadline_offset_ms",
                    timing.completion_deadline_offset_ms(),
                ),
            ]
            .into_iter()
            .filter_map(move |(field, bound)| {
                let bound_ms = bound.filter(|bound| offset.declared_ready_ms > *bound)?;
                Some(PlanTimingConflict {
                    task_index,
                    field,
                    bound_ms,
                    declared_ready_ms: offset.declared_ready_ms,
                })
            })
        })
}

/// Refuse a newly submitted plan whose own declarations contradict its dependencies.
pub fn validate_plan_dependency_windows(plan: &MissionPlan) -> Result<(), OrchestrationError> {
    let offsets = project_plan_timing(plan, &BTreeMap::new());
    if let Some(conflict) = plan_timing_conflicts(plan, &offsets).next() {
        return Err(OrchestrationError::InvalidContract(conflict.message(plan)));
    }
    Ok(())
}
