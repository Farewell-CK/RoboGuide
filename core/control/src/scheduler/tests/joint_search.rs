//! Bounded joint-search scheduler tests.

use super::*;

/// Joint scheduling rejects Candidate Sets with duplicate or surplus Role entries.
#[test]
fn joint_scheduler_requires_exact_unique_candidate_roles() {
    let role = scheduled_role("compute", CapabilityKind::Compute, vec![]);
    let task = requirement("mission-exact", "task-exact", vec![role.clone()]);
    let node_id = NodeId::new("node-a").expect("node id valid");
    for roles in [
        vec![
            RoleCandidates::new(role.role_id().clone(), vec![node_id.clone()]),
            RoleCandidates::new(role.role_id().clone(), vec![node_id.clone()]),
        ],
        vec![
            RoleCandidates::new(role.role_id().clone(), vec![node_id.clone()]),
            RoleCandidates::new(
                RoleId::new("surplus").expect("role id valid"),
                vec![node_id.clone()],
            ),
        ],
    ] {
        let candidates = CandidateSet::new(task.task_ref().clone(), roles);
        assert!(matches!(
            BoundedJointScheduler::new().schedule_task_with_snapshot(
                &InMemorySharedNodeState::new(),
                &task,
                &candidates,
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(0),
            ),
            Err(SchedulerError::InvalidCandidateSet(reason))
                if reason == "normal candidates must exactly and uniquely cover Task roles"
        ));
    }
}

/// Default construction retains the production search budget instead of disabling search.
#[test]
fn joint_scheduler_default_matches_new_policy() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Compute, vec![]),
    )
    .expect("node records");
    let role = scheduled_role("compute", CapabilityKind::Compute, vec![]);
    let task = requirement("mission-default", "task-default", vec![role.clone()]);
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node id valid")],
        )],
    );

    assert!(matches!(
        BoundedJointScheduler::default().schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        ),
        Ok(TaskSchedulingOutcome::SelectedNow(_))
    ));
}

/// Joint search backtracks across Roles instead of accepting a greedy dead end.
#[test]
fn joint_scheduler_finds_cross_role_resource_assignment() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration(
            "node-a",
            CapabilityKind::Compute,
            vec![("compute-a", ResourceKind::Compute)],
        ),
    )
    .expect("node-a records");
    record_node(
        &mut state,
        registration(
            "node-b",
            CapabilityKind::Compute,
            vec![("compute-b", ResourceKind::Compute)],
        ),
    )
    .expect("node-b records");
    let task = requirement(
        "mission-joint",
        "task-joint",
        vec![
            scheduled_role(
                "flexible",
                CapabilityKind::Compute,
                vec![(ResourceKind::Compute, 1)],
            ),
            scheduled_role(
                "fixed",
                CapabilityKind::Compute,
                vec![(ResourceKind::Compute, 1)],
            ),
        ],
    );
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![
            RoleCandidates::new(
                RoleId::new("flexible").expect("role valid"),
                vec![
                    NodeId::new("node-a").expect("node valid"),
                    NodeId::new("node-b").expect("node valid"),
                ],
            ),
            RoleCandidates::new(
                RoleId::new("fixed").expect("role valid"),
                vec![NodeId::new("node-a").expect("node valid")],
            ),
        ],
    );

    let outcome = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        )
        .expect("joint search succeeds");
    let TaskSchedulingOutcome::SelectedNow(decision) = outcome else {
        panic!("task should be immediately schedulable");
    };
    assert_eq!(decision.selections()[0].node_id().as_str(), "node-b");
    assert_eq!(decision.selections()[1].node_id().as_str(), "node-a");
}

/// A busy resource moves a bounded-duration Ready Task to the first release boundary.
#[test]
fn joint_scheduler_selects_future_interval() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration(
            "node-a",
            CapabilityKind::Compute,
            vec![("compute-a", ResourceKind::Compute)],
        ),
    )
    .expect("node records");
    let role = scheduled_role(
        "worker",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 1)],
    );
    let task = TaskRequirement::new_scheduled(
        MissionId::new("mission-future").expect("mission valid"),
        TaskId::new("task-future").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(0, Some(20), Some(20), Some(5)).expect("timing valid"),
    )
    .expect("task valid");
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node valid")],
        )],
    );
    let snapshot = SchedulingSnapshot::new(
        7,
        vec![SchedulingOccupancy::new(
            ResourceId::new("compute-a").expect("resource valid"),
            TimestampMs::new(0),
            Some(TimestampMs::new(10)),
        )],
    );

    let outcome = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &snapshot,
            TimestampMs::new(0),
            TimestampMs::new(0),
        )
        .expect("future search succeeds");
    let TaskSchedulingOutcome::SelectedFuture(decision) = outcome else {
        panic!("busy resource should produce a future interval");
    };
    assert_eq!(decision.starts_at(), TimestampMs::new(10));
    assert_eq!(decision.ends_at(), Some(TimestampMs::new(15)));
    assert_eq!(decision.latest_activation_at(), Some(TimestampMs::new(15)));
    assert_eq!(decision.snapshot_version(), 7);
}

/// One Role can jointly require multiple exclusive resource categories and minimum capacities.
#[test]
fn joint_scheduler_selects_all_declared_resource_dimensions() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration_with_capacity(
            "node-a",
            CapabilityKind::Compute,
            vec![
                ("compute-small", ResourceKind::Compute, 1),
                ("compute-large", ResourceKind::Compute, 4),
                ("space-a", ResourceKind::Space, 2),
            ],
        ),
    )
    .expect("node records");
    let role = scheduled_role(
        "worker",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 3), (ResourceKind::Space, 2)],
    );
    let task = requirement("mission-sized", "task-sized", vec![role.clone()]);
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node valid")],
        )],
    );

    let TaskSchedulingOutcome::SelectedNow(decision) = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        )
        .expect("joint sizing succeeds")
    else {
        panic!("sized Task should be immediately selected");
    };
    assert_eq!(
        decision.selections()[0]
            .resource_ids()
            .iter()
            .map(ResourceId::as_str)
            .collect::<Vec<_>>(),
        vec!["compute-large", "space-a"]
    );
    assert_eq!(decision.selections()[0].resource_units(), &[3, 2]);
}

/// The configured bound is one budget for the whole joint decision, not each time candidate.
#[test]
fn joint_scheduler_reports_search_budget_exhaustion() {
    let mut state = InMemorySharedNodeState::new();
    for (node, resource) in [("node-a", "compute-a"), ("node-b", "compute-b")] {
        record_node(
            &mut state,
            registration(
                node,
                CapabilityKind::Compute,
                vec![(resource, ResourceKind::Compute)],
            ),
        )
        .expect("node records");
    }
    let flexible = scheduled_role(
        "flexible",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 1)],
    );
    let fixed = scheduled_role(
        "fixed",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 1)],
    );
    let task = requirement(
        "mission-budget",
        "task-budget",
        vec![flexible.clone(), fixed.clone()],
    );
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![
            RoleCandidates::new(
                flexible.role_id().clone(),
                vec![
                    NodeId::new("node-a").expect("node valid"),
                    NodeId::new("node-b").expect("node valid"),
                ],
            ),
            RoleCandidates::new(
                fixed.role_id().clone(),
                vec![NodeId::new("node-a").expect("node valid")],
            ),
        ],
    );

    assert_eq!(
        BoundedJointScheduler::with_max_expansions(1).schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        ),
        Err(SchedulerError::SearchLimited)
    );
}

/// Missed start windows and unbounded future occupancy remain explicit non-selections.
#[test]
fn joint_scheduler_distinguishes_window_miss_and_unbounded_future() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Compute, Vec::new()),
    )
    .expect("node records");
    let role = scheduled_role("worker", CapabilityKind::Compute, Vec::new());
    let candidates_for = |task: &TaskRequirement| {
        CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role.role_id().clone(),
                vec![NodeId::new("node-a").expect("node valid")],
            )],
        )
    };
    let missed = TaskRequirement::new_scheduled(
        MissionId::new("mission-window").expect("mission valid"),
        TaskId::new("missed").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(0, Some(5), None, Some(1)).expect("timing valid"),
    )
    .expect("task valid");
    assert_eq!(
        BoundedJointScheduler::new()
            .schedule_task_with_snapshot(
                &state,
                &missed,
                &candidates_for(&missed),
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(6),
            )
            .expect("window result is valid"),
        TaskSchedulingOutcome::WindowMissed
    );

    let deadline_missed = TaskRequirement::new_scheduled(
        MissionId::new("mission-window").expect("mission valid"),
        TaskId::new("deadline-missed").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(0, None, Some(10), Some(5)).expect("timing valid"),
    )
    .expect("task valid");
    assert_eq!(
        BoundedJointScheduler::new()
            .schedule_task_with_snapshot(
                &state,
                &deadline_missed,
                &candidates_for(&deadline_missed),
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(6),
            )
            .expect("completion deadline result is valid"),
        TaskSchedulingOutcome::WindowMissed
    );

    let unbounded = TaskRequirement::new_scheduled(
        MissionId::new("mission-window").expect("mission valid"),
        TaskId::new("unbounded").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(10, None, None, None).expect("timing valid"),
    )
    .expect("task valid");
    assert_eq!(
        BoundedJointScheduler::new()
            .schedule_task_with_snapshot(
                &state,
                &unbounded,
                &candidates_for(&unbounded),
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(0),
            )
            .expect("unbounded future result is valid"),
        TaskSchedulingOutcome::Deferred
    );
}

/// Source-aware estimates bound scheduling without becoming Mission-authored constraints.
#[test]
fn joint_scheduler_consumes_fresh_external_duration_evidence() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Compute, Vec::new()),
    )
    .expect("node records");
    let role = scheduled_role("worker", CapabilityKind::Compute, Vec::new());
    let task = TaskRequirement::new_scheduled(
        MissionId::new("mission-estimate").expect("mission valid"),
        TaskId::new("task-estimate").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new_constraints(0, None, Some(50)).expect("constraints valid"),
    )
    .expect("task valid");
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node valid")],
        )],
    );
    let estimate = domain::TaskDurationEstimate::new(
        task.task_ref().clone(),
        20,
        domain::StateSource::roboguide("duration-profile").expect("source valid"),
        TimestampMs::new(10),
        100,
    )
    .expect("estimate valid");

    let TaskSchedulingOutcome::SelectedNow(decision) = BoundedJointScheduler::new()
        .schedule_task_with_snapshot_and_estimate(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TaskSchedulingContext::new(TimestampMs::new(0), TimestampMs::new(10), Some(&estimate)),
        )
        .expect("fresh estimate schedules")
    else {
        panic!("task should schedule immediately");
    };
    assert_eq!(decision.ends_at(), Some(TimestampMs::new(30)));
    assert_eq!(decision.latest_activation_at(), Some(TimestampMs::new(30)));
    assert_eq!(decision.duration_estimate(), Some(&estimate));
}

/// Scheduler rejects stale and cross-Task duration evidence before selecting resources.
#[test]
fn joint_scheduler_rejects_invalid_duration_evidence() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Compute, Vec::new()),
    )
    .expect("node records");
    let role = scheduled_role("worker", CapabilityKind::Compute, Vec::new());
    let task = requirement("mission-estimate", "task-estimate", vec![role.clone()]);
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node valid")],
        )],
    );
    let source = domain::StateSource::roboguide("duration-profile").expect("source valid");
    let stale = domain::TaskDurationEstimate::new(
        task.task_ref().clone(),
        20,
        source.clone(),
        TimestampMs::new(10),
        5,
    )
    .expect("estimate valid");
    let wrong_task = domain::TaskDurationEstimate::new(
        TaskRef::new(
            MissionId::new("mission-estimate").expect("mission valid"),
            TaskId::new("other-task").expect("task valid"),
        ),
        20,
        source,
        TimestampMs::new(20),
        100,
    )
    .expect("estimate valid");
    let scheduler = BoundedJointScheduler::new();

    for estimate in [&stale, &wrong_task] {
        assert!(matches!(
            scheduler.schedule_task_with_snapshot_and_estimate(
                &state,
                &task,
                &candidates,
                &SchedulingSnapshot::empty(),
                TaskSchedulingContext::new(
                    TimestampMs::new(0),
                    TimestampMs::new(20),
                    Some(estimate),
                ),
            ),
            Err(SchedulerError::InvalidDurationEstimate(_))
        ));
    }
}
