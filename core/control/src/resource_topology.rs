//! Original deployment topology constraints composed with the existing reservation authority.

use crate::{ControlError, ControlPlane, SchedulingReservationPhase, SchedulingSnapshot};
use domain::{
    AllocationOwner, NodeRegistration, ResourceId, ResourceKind, ResourceTopologyRegistry,
    ResourceTopologyReservation, RoleAssignment, RoleId, TaskRef, TimestampMs,
};
use ports::SharedNodeStateReader;
use std::collections::BTreeSet;

/// Compares half-open logical intervals; an unresolved end remains a blocker.
pub(crate) fn overlaps(
    left: TimestampMs,
    left_end: Option<TimestampMs>,
    right: TimestampMs,
    right_end: Option<TimestampMs>,
) -> bool {
    left_end.is_none_or(|end| right < end) && right_end.is_none_or(|end| left < end)
}

impl ControlPlane {
    /// Returns committed deployment declarations, never a current device inventory.
    pub fn resource_topology_registry(&self) -> Option<&ResourceTopologyRegistry> {
        self.resource_topology_registry.as_ref()
    }

    /// Reports exact original evidence through a bounded read-only administration view.
    pub fn resource_topology_reservations(
        &self,
    ) -> Vec<(
        &ResourceId,
        &TaskRef,
        &RoleId,
        &Option<domain::ExecutionGroupId>,
        &ResourceTopologyReservation,
    )> {
        self.reservations
            .iter()
            .filter_map(|(id, held)| {
                held.topology
                    .as_ref()
                    .map(|original| (id, &held.task_ref, &held.role_id, &held.group_id, original))
            })
            .collect()
    }

    /// Installs a complete revision only after protecting every active, Unknown and future owner.
    pub fn accept_resource_topology_registry(
        &mut self,
        registry: ResourceTopologyRegistry,
        expected: u64,
    ) -> Result<(), ControlError> {
        registry.validate().map_err(ControlError::InvalidProposal)?;
        let old = self.resource_topology_registry.as_ref();
        let revision = old.map_or(0, |old| old.revision);
        if revision != expected
            || expected.checked_add(1) != Some(registry.revision)
            || old.is_some_and(|old| old.registry_id != registry.registry_id)
            || self.resource_topology_history.len() >= 1024
        {
            return Err(ControlError::InvalidProposal(
                "resource topology original identity/revision conflicts".into(),
            ));
        }
        let held = self.reservations.keys().cloned().chain(
            self.scheduled_tasks
                .values()
                .filter(|record| record.phase() != SchedulingReservationPhase::Invalidated)
                .flat_map(|record| record.decision().selections())
                .flat_map(|selection| selection.resource_ids().iter().cloned()),
        );
        for id in held {
            if old.and_then(|old| old.entry(&id)) != registry.entry(&id) {
                return Err(ControlError::InvalidProposal(
                    "held resource topology cannot be added, removed or changed".into(),
                ));
            }
        }
        let version = self.calendar_version.checked_add(1).ok_or_else(|| {
            ControlError::InvalidProposal("resource topology calendar generation exhausted".into())
        })?;
        self.resource_topology_history
            .insert(registry.revision, registry.clone());
        self.resource_topology_registry = Some(registry);
        self.calendar_version = version;
        Ok(())
    }

    /// Requires explicit registry entries for every declared Space/Time resource once opted in.
    pub(crate) fn validate_topology_registration(
        &self,
        registration: &NodeRegistration,
    ) -> Result<(), ControlError> {
        let Some(registry) = &self.resource_topology_registry else {
            return Ok(());
        };
        for resource in registration.resources() {
            if resource.kind() == ResourceKind::Compute {
                if registry.entry(resource.id()).is_some() {
                    return Err(ControlError::InvalidProposal(
                        "Compute resource cannot hide spatial/calendar interpretation".into(),
                    ));
                }
                continue;
            }
            if registry.entry(resource.id()).is_none_or(|entry| {
                &entry.node_id != registration.node_id() || entry.kind != resource.kind()
            }) {
                return Err(ControlError::InvalidProposal(
                    "Space/Time declaration lacks exact current deployment topology".into(),
                ));
            }
        }
        for held in self
            .reservations
            .values()
            .filter_map(|held| held.topology.as_ref())
        {
            if &held.entry.node_id == registration.node_id()
                && !registration.resources().iter().any(|resource| {
                    resource.id() == &held.entry.resource_id && resource.kind() == held.entry.kind
                })
            {
                return Err(ControlError::InvalidProposal(
                    "registration removed an original spatial/calendar resource".into(),
                ));
            }
        }
        Ok(())
    }

    /// Distinguishes exact same-Context reuse from another Task's unrelated reservation.
    fn topology_owner_reusable(
        &self,
        held: &crate::coordination::Reservation,
        task: &TaskRef,
        role: &RoleId,
    ) -> bool {
        let Some(group) = &held.group_id else {
            return false;
        };
        matches!(held.owner, AllocationOwner::Context { .. })
            && self
                .capacity_owner(group, task, role)
                .is_ok_and(|owner| owner == held.owner)
    }

    /// Rechecks the complete normal, Group or recovery batch before any quota/event mutation.
    pub(crate) fn validate_topology_assignments<S: SharedNodeStateReader>(
        &self,
        state: &S,
        task: &TaskRef,
        assignments: &[RoleAssignment],
        now: TimestampMs,
    ) -> Result<(), ControlError> {
        let Some(registry) = &self.resource_topology_registry else {
            return Ok(());
        };
        let end = self
            .scheduled_task(task)
            .and_then(|record| record.decision().ends_at());
        let mut selected = Vec::new();
        for assignment in assignments {
            let node = state
                .node(assignment.node_id())
                .ok_or_else(|| ControlError::UnknownNode(assignment.node_id().clone()))?;
            self.validate_topology_registration(node.registration())?;
            for id in assignment.resource_ids() {
                let Some(entry) = registry.entry(id) else {
                    continue;
                };
                if &entry.node_id != assignment.node_id() || !entry.permits_interval(now, end) {
                    return Err(ControlError::AssignmentUnavailable(
                        "original resource owner or complete time opening is unavailable".into(),
                    ));
                }
                if selected
                    .iter()
                    .any(|previous: &&domain::ResourceTopologyEntry| entry.conflicts(previous))
                {
                    return Err(ControlError::AssignmentUnavailable(
                        "joint Role batch has intersecting exclusive spatial declarations".into(),
                    ));
                }
                for (held_id, held) in &self.reservations {
                    let original = held.topology.as_ref().map(|evidence| &evidence.entry);
                    if original.is_some_and(|original| entry.conflicts(original))
                        && !(id == held_id
                            && self.topology_owner_reusable(held, task, assignment.role_id())
                            && held.topology.as_ref().is_some_and(|original| {
                                original.planned_end.is_none_or(|limit| {
                                    end.is_some_and(|end| end <= limit) && now < limit
                                })
                            }))
                    {
                        return Err(ControlError::ResourceConflict {
                            resource_id: id.clone(),
                            owner_task_ref: held.task_ref.clone(),
                            owner_role_id: held.role_id.clone(),
                        });
                    }
                }
                for future in self.scheduled_tasks.values().filter(|future| {
                    future.phase() == SchedulingReservationPhase::Scheduled
                        && future.task_ref() != task
                        && overlaps(
                            now,
                            end,
                            future.decision().starts_at(),
                            future.decision().ends_at(),
                        )
                }) {
                    for selection in future.decision().selections() {
                        if selection.resource_ids().iter().any(|other| {
                            registry
                                .entry(other)
                                .is_some_and(|other| entry.conflicts(other))
                        }) {
                            return Err(ControlError::ResourceConflict {
                                resource_id: id.clone(),
                                owner_task_ref: future.task_ref().clone(),
                                owner_role_id: selection.role_id().clone(),
                            });
                        }
                    }
                }
                selected.push(entry);
            }
        }
        Ok(())
    }

    /// Captures original policy and interval on the same reservation after complete batch validation.
    pub(crate) fn original_topology_reservation(
        &self,
        id: &ResourceId,
        task: &TaskRef,
        now: TimestampMs,
        context_lifetime: bool,
    ) -> Option<ResourceTopologyReservation> {
        let current = self.resource_topology_registry.as_ref()?;
        let registry = self
            .scheduled_task(task)
            .and_then(|record| self.topology_booking_registry(record.decision()).ok())
            .unwrap_or(current);
        let entry = registry.entry(id)?;
        let task_end = self
            .scheduled_task(task)
            .and_then(|record| record.decision().ends_at());
        let planned_end = if context_lifetime {
            entry
                .windows
                .iter()
                .find(|window| {
                    window.starts_at <= now && task_end.is_some_and(|end| end <= window.ends_at)
                })
                .map(|window| window.ends_at)
        } else {
            task_end
        };
        Some(ResourceTopologyReservation {
            revision: registry.revision,
            entry: entry.clone(),
            admitted_at: now,
            planned_end,
        })
    }

    /// Attaches explicit policy to a stateless scheduling input without exporting mutation authority.
    pub(crate) fn with_resource_topology(
        &self,
        mut snapshot: SchedulingSnapshot,
    ) -> SchedulingSnapshot {
        snapshot.resource_topology = self.resource_topology_registry.clone();
        snapshot
    }

    /// Supplies only the original remaining Task interval to recovery selection, with no extension.
    pub fn recovery_scheduling_snapshot(
        &self,
        now: TimestampMs,
        group: &domain::ExecutionGroupId,
        task: &TaskRef,
    ) -> Result<SchedulingSnapshot, ControlError> {
        if self
            .group(group)
            .and_then(|group| group.task_execution(task))
            .is_none()
        {
            return Err(ControlError::InvalidProposal(
                "recovery calendar lacks its exact original Group/Task".into(),
            ));
        }
        let mut snapshot = self.scheduling_snapshot(now);
        snapshot.topology_recovery_interval = self
            .scheduled_task(task)
            .filter(|record| record.group_id() == group)
            .and_then(|record| record.decision().ends_at())
            .filter(|end| *end > now)
            .map(|end| (task.clone(), end));
        Ok(snapshot)
    }

    /// Validates complete joint geometry before a future decision becomes durable authority.
    pub(crate) fn validate_topology_decision(
        &self,
        decision: &crate::TaskSchedulingDecision,
    ) -> Result<(), ControlError> {
        let Some(registry) = &self.resource_topology_registry else {
            return Ok(());
        };
        if !decision
            .selections()
            .iter()
            .flat_map(|selection| selection.resource_ids())
            .any(|id| registry.entry(id).is_some())
        {
            return Ok(());
        }
        self.topology_booking_registry(decision)?;
        let mut selected: Vec<&domain::ResourceTopologyEntry> = Vec::new();
        for selection in decision.selections() {
            for id in selection.resource_ids() {
                if let Some(entry) = registry.entry(id) {
                    if &entry.node_id != selection.node_id()
                        || !entry.permits_interval(decision.starts_at(), decision.ends_at())
                        || selected.iter().any(|other| entry.conflicts(other))
                    {
                        return Err(ControlError::AssignmentUnavailable(
                            "complete future spatial/calendar selection conflicts".into(),
                        ));
                    }
                    selected.push(entry);
                }
            }
        }
        Ok(())
    }

    /// Resolves a frozen deployment booking source and original age without relabelling measurements.
    fn topology_booking_registry(
        &self,
        decision: &crate::TaskSchedulingDecision,
    ) -> Result<&ResourceTopologyRegistry, ControlError> {
        let current = self.resource_topology_registry.as_ref().ok_or_else(|| {
            ControlError::InvalidProposal("booking has no original topology policy".into())
        })?;
        let mut policy = current;
        if let Some(estimate) = decision.duration_estimate()
            && let domain::StateSource::RoboGuide { component } = estimate.source()
            && component.starts_with("resource-topology-booking/")
        {
            policy = self
                .resource_topology_history
                .values()
                .find(|registry| {
                    component
                        == &format!(
                            "resource-topology-booking/{}/{}",
                            registry.registry_id, registry.revision
                        )
                })
                .ok_or_else(|| {
                    ControlError::InvalidProposal("booking lost original deployment source".into())
                })?;
            if estimate.duration_ms() != policy.booking_duration_ms
                || estimate.valid_for_ms() != policy.booking_max_age_ms
                || decision.starts_at() < estimate.received_at()
                || decision.ends_at().is_none_or(|end| {
                    end.as_millis()
                        .checked_sub(estimate.received_at().as_millis())
                        .is_none_or(|age| age > policy.booking_max_age_ms)
                })
            {
                return Err(ControlError::InvalidProposal(
                    "booking changed original declared budget or Mission age".into(),
                ));
            }
        }
        if decision.ends_at().is_some_and(|end| {
            end.as_millis()
                .checked_sub(decision.starts_at().as_millis())
                .is_none_or(|duration| duration > policy.booking_duration_ms)
        }) {
            return Err(ControlError::InvalidProposal(
                "booking exceeds original deployment duration".into(),
            ));
        }
        Ok(policy)
    }

    /// Rechecks exact held declarations and the original remaining interval before Bind or Execute.
    pub(crate) fn bound_topology_allowed(
        &self,
        task: &TaskRef,
        assignments: &[RoleAssignment],
        now: TimestampMs,
    ) -> bool {
        let Some(registry) = &self.resource_topology_registry else {
            return true;
        };
        if assignments
            .iter()
            .flat_map(|assignment| assignment.resource_ids())
            .any(|id| registry.entry(id).is_some())
            && self.scheduled_task(task).is_some_and(|record| {
                record.decision().starts_at() > now
                    || record.decision().ends_at().is_some_and(|end| now >= end)
            })
        {
            return false;
        }
        assignments.iter().all(|assignment| {
            assignment.resource_ids().iter().all(|id| {
                let Some(entry) = registry.entry(id) else {
                    return true;
                };
                &entry.node_id == assignment.node_id()
                    && self.reservations.get(id).is_some_and(|held| {
                        (held.task_ref == *task && held.role_id == *assignment.role_id()
                            || self.topology_owner_reusable(held, task, assignment.role_id()))
                            && held.topology.as_ref().is_some_and(|original| {
                                original.entry == *entry
                                    && original.admitted_at <= now
                                    && original.planned_end.is_none_or(|end| now < end)
                                    && entry.permits_interval(now, original.planned_end)
                            })
                    })
            })
        })
    }

    /// Exposes original held targets requiring safe stop; clocks/endpoints never release ownership.
    pub fn expired_topology_targets(
        &self,
        now: TimestampMs,
    ) -> Vec<(domain::ExecutionGroupId, TaskRef, RoleId)> {
        let mut targets = self
            .reservations
            .values()
            .filter_map(|held| {
                let original = held.topology.as_ref()?;
                (now < original.admitted_at || original.planned_end.is_some_and(|end| now >= end))
                    .then(|| {
                        held.group_id.as_ref().map(|group| {
                            (group.clone(), held.task_ref.clone(), held.role_id.clone())
                        })
                    })
                    .flatten()
            })
            .collect::<BTreeSet<_>>();
        if let Some(registry) = &self.resource_topology_registry {
            for record in self.scheduled_tasks.values().filter(|record| {
                record.phase() == SchedulingReservationPhase::Activated
                    && record.decision().ends_at().is_some_and(|end| now >= end)
            }) {
                for selection in record.decision().selections().iter().filter(|selection| {
                    selection
                        .resource_ids()
                        .iter()
                        .any(|id| registry.entry(id).is_some())
                }) {
                    targets.insert((
                        record.group_id().clone(),
                        record.task_ref().clone(),
                        selection.role_id().clone(),
                    ));
                }
            }
        }
        targets.into_iter().collect()
    }

    /// Verifies original history and active reservation joins without recreating live observations.
    pub(crate) fn validate_resource_topology_checkpoint(&self) -> Result<(), ControlError> {
        let mut previous: Option<&ResourceTopologyRegistry> = None;
        if self.resource_topology_history.len() > 1024 {
            return Err(ControlError::InvalidProposal(
                "resource topology history exceeds bound".into(),
            ));
        }
        for (revision, registry) in &self.resource_topology_history {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if *revision != registry.revision
                || previous.map_or(registry.revision != 1, |old| {
                    old.registry_id != registry.registry_id
                        || old.revision.checked_add(1) != Some(*revision)
                })
            {
                return Err(ControlError::InvalidProposal(
                    "resource topology original history is discontinuous".into(),
                ));
            }
            previous = Some(registry);
        }
        if previous != self.resource_topology_registry.as_ref() {
            return Err(ControlError::InvalidProposal(
                "resource topology current generation lacks original history".into(),
            ));
        }
        for (id, held) in &self.reservations {
            let current = self
                .resource_topology_registry
                .as_ref()
                .and_then(|registry| registry.entry(id));
            match (&held.topology, current) {
                (None, None) => {}
                (Some(original), Some(current))
                    if &original.entry == current
                        && &original.entry.resource_id == id
                        && self
                            .resource_topology_history
                            .get(&original.revision)
                            .and_then(|registry| registry.entry(id))
                            == Some(current)
                        && original
                            .entry
                            .permits_interval(original.admitted_at, original.planned_end) => {}
                _ => {
                    return Err(ControlError::InvalidProposal(
                        "held resource lost original topology or complete interval".into(),
                    ));
                }
            }
        }
        let entries = self
            .reservations
            .values()
            .filter_map(|record| record.topology.as_ref())
            .collect::<Vec<_>>();
        for (index, left) in entries.iter().enumerate() {
            if entries
                .iter()
                .skip(index + 1)
                .any(|right| left.entry.conflicts(&right.entry))
            {
                return Err(ControlError::InvalidProposal(
                    "active original spatial reservations conflict".into(),
                ));
            }
        }
        let mut ids = BTreeSet::new();
        for record in self.scheduled_tasks.values() {
            if !record.topology_recovery_assignments.is_empty()
                && (self.resource_topology_registry.is_none()
                    || record.phase() != SchedulingReservationPhase::Activated
                    || record
                        .topology_recovery_assignments
                        .iter()
                        .any(|(role, replacement)| {
                            replacement.role_id() != role
                                || record
                                    .decision()
                                    .selections()
                                    .iter()
                                    .find(|selection| selection.role_id() == role)
                                    .is_none_or(|original| {
                                        original.resource_ids().len()
                                            != replacement.resource_ids().len()
                                    })
                        }))
            {
                return Err(ControlError::InvalidProposal("calendar recovery projection lacks original roles or active topology authority".into()));
            }
        }
        for record in self
            .scheduled_tasks
            .values()
            .filter(|record| record.phase() != SchedulingReservationPhase::Invalidated)
        {
            self.validate_topology_decision(record.decision())?;
        }
        for record in self
            .scheduled_tasks
            .values()
            .filter(|record| record.phase() == SchedulingReservationPhase::Scheduled)
        {
            ids.clear();
            for selection in record.decision().selections() {
                for id in selection.resource_ids() {
                    if let Some(entry) = self
                        .resource_topology_registry
                        .as_ref()
                        .and_then(|registry| registry.entry(id))
                    {
                        if &entry.node_id != selection.node_id()
                            || !entry.permits_interval(
                                record.decision().starts_at(),
                                record.decision().ends_at(),
                            )
                        {
                            return Err(ControlError::InvalidProposal(
                                "future topology original interval/owner changed".into(),
                            ));
                        }
                        if ids.iter().any(|other| {
                            self.resource_topology_registry
                                .as_ref()
                                .and_then(|registry| registry.entry(other))
                                .is_some_and(|other| entry.conflicts(other))
                        }) {
                            return Err(ControlError::InvalidProposal(
                                "future joint spatial choices conflict".into(),
                            ));
                        }
                        ids.insert(id.clone());
                    }
                }
            }
        }
        let future = self
            .scheduled_tasks
            .values()
            .filter(|record| record.phase() == SchedulingReservationPhase::Scheduled)
            .collect::<Vec<_>>();
        for (index, first) in future.iter().enumerate() {
            for later in future.iter().skip(index + 1) {
                if !overlaps(
                    first.decision().starts_at(),
                    first.decision().ends_at(),
                    later.decision().starts_at(),
                    later.decision().ends_at(),
                ) {
                    continue;
                }
                let intersects = first
                    .decision()
                    .selections()
                    .iter()
                    .flat_map(|selection| selection.resource_ids())
                    .any(|left| {
                        later
                            .decision()
                            .selections()
                            .iter()
                            .flat_map(|selection| selection.resource_ids())
                            .any(|right| {
                                self.resource_topology_registry
                                    .as_ref()
                                    .is_some_and(|registry| {
                                        registry
                                            .entry(left)
                                            .zip(registry.entry(right))
                                            .is_some_and(|(left, right)| left.conflicts(right))
                                    })
                            })
                    });
                if intersects {
                    return Err(ControlError::InvalidProposal(
                        "retained future spatial calendars intersect".into(),
                    ));
                }
            }
        }
        Ok(())
    }
}
