use super::*;
use domain::{
    AllocationOwner, AllocationPhase, ExecutionGroupId, MissionId, RoleId, TaskId, TaskRef,
};

/// Builds one projected allocation for deterministic state tests.
fn allocation(resource_id: &str) -> ResourceAllocation {
    ResourceAllocation::new(
        ResourceId::new(resource_id).expect("test resource id must be valid"),
        TaskRef::new(
            MissionId::new("mission-a").expect("test mission id must be valid"),
            TaskId::new("task-a").expect("test task id must be valid"),
        ),
        RoleId::new("transport").expect("test role id must be valid"),
        Some(ExecutionGroupId::new("group-a").expect("test group id must be valid")),
        AllocationPhase::Bound,
        AllocationOwner::Task(TaskRef::new(
            MissionId::new("mission-a").expect("test mission id must be valid"),
            TaskId::new("task-a").expect("test task id must be valid"),
        )),
    )
}

/// Reader order is stable regardless of incoming snapshot order.
#[test]
fn allocation_reader_returns_stable_resource_order() {
    let mut state = InMemoryAllocationState::new();
    state
        .replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(10),
            vec![
                allocation("resource-z"),
                allocation("resource-a"),
                allocation("resource-m"),
            ],
        ))
        .expect("initial allocation projection should be accepted");

    let resource_ids = state
        .allocations()
        .into_iter()
        .map(|record| record.resource_id().as_str())
        .collect::<Vec<_>>();
    assert_eq!(resource_ids, vec!["resource-a", "resource-m", "resource-z"]);
}

/// A newer whole-view projection atomically replaces the prior view.
#[test]
fn newer_allocation_projection_replaces_older_view() {
    let mut state = InMemoryAllocationState::new();
    state
        .replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(10),
            vec![allocation("resource-a")],
        ))
        .expect("initial allocation projection should be accepted");
    state
        .replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(20),
            vec![allocation("resource-b")],
        ))
        .expect("newer allocation projection should replace the view");

    assert!(
        state
            .allocation(&ResourceId::new("resource-a").expect("test id must be valid"))
            .is_none()
    );
    assert!(
        state
            .allocation(&ResourceId::new("resource-b").expect("test id must be valid"))
            .is_some()
    );
    assert_eq!(state.snapshot_projected_at(), Some(TimestampMs::new(20)));
}

/// An older snapshot is rejected without changing current records.
#[test]
fn older_allocation_projection_is_rejected() {
    let mut state = InMemoryAllocationState::new();
    state
        .replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(20),
            vec![allocation("resource-current")],
        ))
        .expect("current allocation projection should be accepted");

    assert!(matches!(
        state.replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(10),
            vec![allocation("resource-old")],
        )),
        Err(AllocationStateError::StaleProjection { .. })
    ));
    assert!(
        state
            .allocation(&ResourceId::new("resource-current").expect("test id must be valid"))
            .is_some()
    );
    assert_eq!(state.snapshot_projected_at(), Some(TimestampMs::new(20)));
}

/// Duplicate resources are rejected before replacing any current state.
#[test]
fn duplicate_allocation_resource_is_rejected_atomically() {
    let mut state = InMemoryAllocationState::new();
    state
        .replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(10),
            vec![allocation("resource-current")],
        ))
        .expect("current allocation projection should be accepted");

    assert!(matches!(
        state.replace_allocation_view(AllocationViewSnapshot::new(
            TimestampMs::new(20),
            vec![allocation("resource-a"), allocation("resource-a")],
        )),
        Err(AllocationStateError::DuplicateResource(_))
    ));
    assert!(
        state
            .allocation(&ResourceId::new("resource-current").expect("test id must be valid"))
            .is_some()
    );
    assert_eq!(state.snapshot_projected_at(), Some(TimestampMs::new(10)));
}
