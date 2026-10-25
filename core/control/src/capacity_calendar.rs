//! Control-owned quantitative calendar projection, preserving original Context envelopes.

use crate::resource_allocations::CapacityLedger;
use crate::scheduler::capacity::{ContextCapacity, interval_available};
use crate::{
    ControlError, ControlPlane, SchedulingOccupancy, SchedulingReservationPhase,
    SchedulingRoleConstraint, SchedulingSnapshot, TaskSchedulingDecision,
};
use domain::{
    AllocationOwner, ExecutionGroupId, ResourceAllocationMode, ResourceBindingScope, ResourceId,
    TaskRef, TimestampMs,
};
use ports::SharedNodeStateReader;
use std::collections::{BTreeMap, BTreeSet};

/// Canonical original Context owner axis; this is bookkeeping, never a Node or ResourceId.
fn context_axis(group: &ExecutionGroupId, owner: &AllocationOwner) -> String {
    serde_json::to_string(&(group, owner)).expect("typed original Context identity serializes")
}

impl ControlPlane {
    /// Rechecks retained quantitative future evidence using original capacities without inventing fresh State.
    pub(crate) fn validate_capacity_calendar_history(&self) -> Result<(), ControlError> {
        let mut pools = BTreeMap::<ResourceId, (domain::NodeId, String, u32)>::new();
        let mut axes = BTreeMap::<(ResourceId, String), u32>::new();
        for record in self.capacity_ledger.allocations.values() {
            let original = &record.original;
            pools.insert(
                original.resource_id.clone(),
                (
                    original.node_id.clone(),
                    original.resource_class.clone(),
                    original.admitted_capacity,
                ),
            );
            if matches!(original.owner, AllocationOwner::Context { .. }) {
                let units = self.context_capacity_units(
                    &original.group_id,
                    &original.owner,
                    Some(&original.resource_class),
                );
                if units != Some(original.allocation_units) {
                    return Err(ControlError::InvalidProposal(
                        "restored Context allocation changed its original envelope".into(),
                    ));
                }
                axes.insert(
                    (
                        original.resource_id.clone(),
                        context_axis(&original.group_id, &original.owner),
                    ),
                    original.allocation_units,
                );
            }
        }
        for future in self.scheduled_tasks.values() {
            for selection in future.decision().selections() {
                for ((id, demand), capacity) in selection
                    .resource_ids()
                    .iter()
                    .zip(selection.resource_requirements())
                    .zip(selection.resource_capacities())
                {
                    if demand.allocation_mode() != ResourceAllocationMode::Divisible {
                        continue;
                    }
                    let class = demand
                        .resource_class()
                        .filter(|value| !value.trim().is_empty() && value.len() <= 128)
                        .ok_or_else(|| {
                            ControlError::InvalidProposal(
                                "retained shared selection has no typed units".into(),
                            )
                        })?;
                    if demand.units() == 0
                        || demand.kind() != domain::ResourceKind::Compute
                        || *capacity < demand.units()
                    {
                        return Err(ControlError::InvalidProposal(
                            "retained shared selection has invalid original capacity".into(),
                        ));
                    }
                    if let Some((node, old_class, limit)) = pools.get_mut(id) {
                        if node != selection.node_id() || old_class != class {
                            return Err(ControlError::InvalidProposal(
                                "retained pool changed original Node or unit class".into(),
                            ));
                        }
                        *limit = (*limit).max(*capacity);
                    } else {
                        pools.insert(
                            id.clone(),
                            (selection.node_id().clone(), class.into(), *capacity),
                        );
                    }
                    let owner = self.capacity_owner(
                        future.group_id(),
                        future.task_ref(),
                        selection.role_id(),
                    )?;
                    if matches!(owner, AllocationOwner::Context { .. }) {
                        let limit = self
                            .context_capacity_units(future.group_id(), &owner, Some(class))
                            .ok_or_else(|| {
                                ControlError::InvalidProposal(
                                    "retained future Context lacks its original envelope".into(),
                                )
                            })?;
                        if demand.units() > limit || limit > *capacity {
                            return Err(ControlError::InvalidProposal(
                                "retained future Context exceeds original capacity".into(),
                            ));
                        }
                        axes.insert((id.clone(), context_axis(future.group_id(), &owner)), limit);
                    }
                }
            }
        }
        let snapshot = self.scheduling_snapshot(TimestampMs::new(0));
        for (id, (_, _, capacity)) in pools {
            if !interval_available(
                snapshot
                    .occupancies()
                    .iter()
                    .filter(|item| item.resource_id() == &id && item.owner_axis.is_none()),
                TimestampMs::new(0),
                None,
                0,
                capacity,
            ) {
                return Err(ControlError::InvalidProposal(
                    "restored future resource quantities overlap beyond original capacity".into(),
                ));
            }
        }
        for ((id, axis), capacity) in axes {
            if !interval_available(
                snapshot.occupancies().iter().filter(|item| {
                    item.resource_id() == &id && item.owner_axis.as_ref() == Some(&axis)
                }),
                TimestampMs::new(0),
                None,
                0,
                capacity,
            ) {
                return Err(ControlError::InvalidProposal(
                    "restored Context borrowers exceed their original envelope".into(),
                ));
            }
        }
        Ok(())
    }

    /// Uses only an unexpired original planning end; overruns and unscheduled work retain no known end.
    fn capacity_planning_end(&self, task: &TaskRef, now: TimestampMs) -> Option<TimestampMs> {
        self.scheduled_tasks
            .get(task)
            .filter(|item| item.phase() != SchedulingReservationPhase::Invalidated)
            .and_then(|item| item.decision().ends_at())
            .filter(|end| *end > now)
    }

    /// Projects complete physical parents and separate borrower axes; Context capacity never auto-expires.
    pub(crate) fn quantitative_occupancies(
        &self,
        ledger: &CapacityLedger,
        now: TimestampMs,
        exclude_future: Option<&TaskRef>,
    ) -> Vec<SchedulingOccupancy> {
        let mut result = Vec::new();
        let mut parents = BTreeSet::new();
        for record in ledger.allocations.values() {
            let original = &record.original;
            let context = matches!(original.owner, AllocationOwner::Context { .. });
            let end = if context {
                None
            } else {
                self.capacity_planning_end(&original.task_ref, now)
            };
            result.push(SchedulingOccupancy::quantitative(
                original.resource_id.clone(),
                now,
                end,
                original.allocation_units,
                None,
            ));
            if context {
                let axis = context_axis(&original.group_id, &original.owner);
                parents.insert((original.resource_id.clone(), axis.clone()));
                for borrower in &record.borrowers {
                    result.push(SchedulingOccupancy::quantitative(
                        original.resource_id.clone(),
                        now,
                        self.capacity_planning_end(&borrower.task_ref, now),
                        borrower.units,
                        Some(axis.clone()),
                    ));
                }
            }
        }
        let mut future_parents = BTreeMap::<(ResourceId, String), (TimestampMs, u32)>::new();
        for reservation in self.scheduled_tasks.values().filter(|item| {
            item.phase() == SchedulingReservationPhase::Scheduled
                && exclude_future != Some(item.task_ref())
        }) {
            let decision = reservation.decision();
            for selection in decision.selections() {
                for (id, demand) in selection
                    .resource_ids()
                    .iter()
                    .zip(selection.resource_requirements())
                {
                    if demand.allocation_mode() != ResourceAllocationMode::Divisible {
                        continue;
                    }
                    // Every new decision was validated against its complete original Group before publication.
                    let owner = self
                        .capacity_owner(
                            reservation.group_id(),
                            decision.task_ref(),
                            selection.role_id(),
                        )
                        .expect("validated original capacity owner");
                    if matches!(owner, AllocationOwner::Context { .. }) {
                        let axis = context_axis(reservation.group_id(), &owner);
                        result.push(SchedulingOccupancy::quantitative(
                            id.clone(),
                            decision.starts_at(),
                            decision.ends_at(),
                            demand.units(),
                            Some(axis.clone()),
                        ));
                        let units = self
                            .context_capacity_units(
                                reservation.group_id(),
                                &owner,
                                demand.resource_class(),
                            )
                            .expect("validated original Context envelope");
                        let key = (id.clone(), axis);
                        if !parents.contains(&key) {
                            future_parents
                                .entry(key)
                                .and_modify(|value| value.0 = value.0.min(decision.starts_at()))
                                .or_insert((decision.starts_at(), units));
                        }
                    } else {
                        result.push(SchedulingOccupancy::quantitative(
                            id.clone(),
                            decision.starts_at(),
                            decision.ends_at(),
                            demand.units(),
                            None,
                        ));
                    }
                }
            }
        }
        result.extend(future_parents.into_iter().map(|((id, _), (start, units))| {
            SchedulingOccupancy::quantitative(id, start, None, units, None)
        }));
        result
    }

    /// Looks up only the accepted original ContextRole envelope with exact semantic units.
    pub(crate) fn context_capacity_units(
        &self,
        group: &ExecutionGroupId,
        owner: &AllocationOwner,
        class: Option<&str>,
    ) -> Option<u32> {
        let AllocationOwner::Context {
            context_id,
            context_role_id,
            ..
        } = owner
        else {
            return None;
        };
        self.group(group)?
            .context_resource_envelopes
            .iter()
            .find(|(context, role)| {
                context == context_id && role.context_role_id() == context_role_id
            })
            .and_then(|(_, role)| {
                role.resource_envelope()
                    .iter()
                    .find(|limit| limit.resource_class() == class)
            })
            .map(|limit| limit.units())
    }

    /// Adds exact original Context budgets and previously frozen planned bindings to one Task snapshot.
    pub(crate) fn with_capacity_contexts(
        &self,
        mut snapshot: SchedulingSnapshot,
        group: &ExecutionGroupId,
        task: &TaskRef,
        now: TimestampMs,
    ) -> Result<SchedulingSnapshot, ControlError> {
        let original = self
            .group(group)
            .ok_or_else(|| ControlError::UnknownGroup(group.clone()))?;
        let execution = original.task_execution(task).ok_or_else(|| {
            ControlError::InvalidProposal("capacity scheduling Task is absent".into())
        })?;
        for role_id in execution.role_scopes().keys() {
            let role = original.role_requirement(task, role_id).ok_or_else(|| {
                ControlError::InvalidProposal("current original scheduling Role is absent".into())
            })?;
            if execution.role_scope(role.role_id()) != ResourceBindingScope::Context {
                continue;
            }
            let demands = role.resource_requirements();
            let Some(demand) = demands
                .iter()
                .find(|demand| demand.allocation_mode() == ResourceAllocationMode::Divisible)
            else {
                continue;
            };
            let owner = self.capacity_owner(group, task, role.role_id())?;
            let axis = context_axis(group, &owner);
            let units = self
                .context_capacity_units(group, &owner, demand.resource_class())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "original scheduling Context envelope is absent".into(),
                    )
                })?;
            let mut parent_starts_at = self
                .capacity_ledger
                .allocations
                .values()
                .any(|record| &record.original.group_id == group && record.original.owner == owner)
                .then_some(now);
            for future in self.scheduled_tasks.values().filter(|item| {
                item.group_id() == group
                    && item.task_ref() != task
                    && item.phase() == SchedulingReservationPhase::Scheduled
            }) {
                for selected in future.decision().selections() {
                    if self.capacity_owner(group, future.task_ref(), selected.role_id())? == owner {
                        parent_starts_at = Some(
                            parent_starts_at.map_or(future.decision().starts_at(), |old| {
                                old.min(future.decision().starts_at())
                            }),
                        );
                        let constraint = SchedulingRoleConstraint::new(
                            role.role_id().clone(),
                            selected.node_id().clone(),
                            selected.resource_ids().to_vec(),
                        );
                        if snapshot
                            .role_constraints
                            .get(role.role_id())
                            .is_some_and(|old| old != &constraint)
                        {
                            return Err(ControlError::InvalidProposal(
                                "original Context has conflicting planned resource bindings".into(),
                            ));
                        }
                        snapshot
                            .role_constraints
                            .insert(role.role_id().clone(), constraint);
                    }
                }
            }
            snapshot.context_capacities.insert(
                role.role_id().clone(),
                ContextCapacity {
                    owner_axis: axis,
                    units,
                    parent_starts_at,
                },
            );
        }
        Ok(snapshot)
    }

    /// Validates a complete joint decision against current weighted and exclusive calendar evidence.
    pub(crate) fn validate_capacity_decision<S: SharedNodeStateReader>(
        &self,
        state: &S,
        snapshot: &SchedulingSnapshot,
        group_id: &ExecutionGroupId,
        decision: &TaskSchedulingDecision,
    ) -> Result<(), ControlError> {
        let mut prior = Vec::new();
        for selection in decision.selections() {
            let node = state
                .node(selection.node_id())
                .ok_or_else(|| ControlError::UnknownNode(selection.node_id().clone()))?;
            let group = self
                .group(group_id)
                .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
            let role = group
                .role_requirement(decision.task_ref(), selection.role_id())
                .expect("validated scheduling Role");
            for (id, demand) in selection
                .resource_ids()
                .iter()
                .zip(role.resource_requirements().iter())
            {
                let resource = node
                    .registration()
                    .resources()
                    .iter()
                    .find(|resource| resource.id() == id)
                    .ok_or_else(|| {
                        ControlError::InvalidProposal("scheduled resource is absent".into())
                    })?;
                if !snapshot.capacity_available(
                    selection.role_id(),
                    resource,
                    demand,
                    decision.starts_at(),
                    decision.ends_at(),
                    &prior,
                ) {
                    return Err(ControlError::AssignmentUnavailable(
                        "joint resource demand conflicts with the original capacity calendar"
                            .into(),
                    ));
                }
            }
            prior.push(selection.clone());
        }
        Ok(())
    }

    /// Refuses actual hard commitments that would invalidate any retained future quantity promise.
    pub(crate) fn validate_capacity_calendar<S: SharedNodeStateReader>(
        &self,
        ledger: &CapacityLedger,
        task: &TaskRef,
        state: &S,
        now: TimestampMs,
    ) -> Result<(), ControlError> {
        let occupancies = self.quantitative_occupancies(ledger, now, Some(task));
        let mut pools = BTreeMap::new();
        for (id, record) in ledger
            .allocations
            .iter()
            .filter(|(id, record)| self.capacity_ledger.allocations.get(*id) != Some(*record))
        {
            let _ = id;
            let resource = state
                .node(&record.original.node_id)
                .and_then(|node| {
                    node.registration()
                        .resources()
                        .iter()
                        .find(|resource| resource.id() == &record.original.resource_id)
                })
                .ok_or_else(|| {
                    ControlError::AssignmentUnavailable(
                        "original allocated pool is unavailable".into(),
                    )
                })?;
            pools.insert(record.original.resource_id.clone(), resource.capacity());
        }
        for (id, capacity) in pools {
            if !interval_available(
                occupancies
                    .iter()
                    .filter(|item| item.resource_id() == &id && item.owner_axis.is_none()),
                now,
                None,
                0,
                capacity,
            ) {
                return Err(ControlError::AssignmentUnavailable(
                    "quantitative Commit conflicts with original future capacity".into(),
                ));
            }
        }
        Ok(())
    }
}
