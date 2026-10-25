//! Exact quantitative Control claims and Context borrowers, independent of observable State.

use crate::{ControlError, ControlPlane, coordination::Reservation};
use domain::{
    AllocationOwner, ExecutionGroupId, NodeId, ResourceAllocationGrant, ResourceBindingScope,
    ResourceId, RoleId, TaskRef,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// One original Task/Role use of a parent allocation; release cannot affect a sibling borrower.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CapacityBorrower {
    /// Original Task identity.
    pub(crate) task_ref: TaskRef,
    /// Original TaskRole identity, which may differ across Tasks sharing a ContextRole.
    pub(crate) role_id: RoleId,
    /// Original positive Task demand.
    pub(crate) units: u32,
}

/// One retained parent allocation; Context claims outlive individual borrowers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CapacityAllocation {
    /// Exact first admitted original grant; borrower fields stay original history.
    pub(crate) original: ResourceAllocationGrant,
    /// Existing Control ownership shape, shared by all lifecycle checks.
    pub(crate) reservation: Reservation,
    /// Current logical borrowers; Unknown is retained until the owning release gate acts.
    pub(crate) borrowers: Vec<CapacityBorrower>,
}

/// Durable original allocation generation and current ownership, with no implicit timer release.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CapacityLedger {
    /// Monotonic original generation; release and cold recovery never reset it.
    pub(crate) generation: u64,
    /// Original allocation IDs mapped to exact claims.
    pub(crate) allocations: BTreeMap<String, CapacityAllocation>,
}

/// Complete current-State input for one Task/Role resource in an atomic Commit batch.
#[derive(Debug, Clone)]
pub(crate) struct CapacityRequest {
    /// Actual selected resource.
    pub(crate) resource_id: ResourceId,
    /// Frozen typed unit interpretation of the selected pool.
    pub(crate) resource_class: String,
    /// Actual current selected Node.
    pub(crate) node_id: NodeId,
    /// Original owning Group.
    pub(crate) group_id: ExecutionGroupId,
    /// Task consuming this exact demand.
    pub(crate) task_ref: TaskRef,
    /// Exact TaskRole.
    pub(crate) role_id: RoleId,
    /// Immutable Mission tenant.
    pub(crate) tenant_id: String,
    /// Task or ContextRole authority.
    pub(crate) owner: AllocationOwner,
    /// Original Task units.
    pub(crate) units: u32,
    /// Frozen Task demand or explicitly declared ContextRole envelope.
    pub(crate) parent_units: u32,
    /// Actual current registered pool capacity.
    pub(crate) capacity: u32,
}

/// Hashes typed original identity; caller delimiters cannot alias another owner or generation.
fn allocation_id(grant: &ResourceAllocationGrant) -> String {
    grant.original_allocation_id()
}

impl CapacityLedger {
    /// Creates an empty original allocation authority without allocating in const Control constructors.
    pub(crate) const fn new() -> Self {
        Self {
            generation: 0,
            allocations: BTreeMap::new(),
        }
    }

    /// Returns a current parent claim only for the exact original Group/resource/owner/TaskRole.
    pub(crate) fn owner_allocation(
        &self,
        request: &CapacityRequest,
    ) -> Option<&CapacityAllocation> {
        self.allocations.values().find(|record| {
            let original = &record.original;
            original.resource_id == request.resource_id
                && original.group_id == request.group_id
                && original.node_id == request.node_id
                && original.owner == request.owner
                && (matches!(request.owner, AllocationOwner::Context { .. })
                    || original.role_id == request.role_id)
        })
    }

    /// Sums actual parent commitments using wide arithmetic; historical source capacities are never summed.
    pub(crate) fn committed_units(&self, resource: &ResourceId) -> u64 {
        self.allocations
            .values()
            .filter(|record| &record.original.resource_id == resource)
            .map(|record| u64::from(record.original.allocation_units))
            .sum()
    }

    /// Admits one borrower into a candidate batch; callers publish only after every resource/quota check.
    pub(crate) fn admit(
        &mut self,
        request: &CapacityRequest,
    ) -> Result<ResourceAllocationGrant, ControlError> {
        if request.units == 0
            || request.units > request.parent_units
            || request.parent_units > request.capacity
            || request.tenant_id.trim().is_empty()
        {
            return Err(ControlError::AssignmentUnavailable(
                "divisible demand exceeds its current declaration".into(),
            ));
        }
        if self.allocations.values().any(|record| {
            record.original.group_id == request.group_id
                && record.original.owner == request.owner
                && (matches!(request.owner, AllocationOwner::Context { .. })
                    || record.original.role_id == request.role_id)
                && (record.original.resource_id != request.resource_id
                    || record.original.node_id != request.node_id)
        }) {
            return Err(ControlError::AssignmentUnavailable(
                "original quantitative owner cannot change its selected pool or Node".into(),
            ));
        }
        if let Some(existing) = self.owner_allocation(request) {
            if existing.original.resource_class != request.resource_class
                || existing.original.tenant_id != request.tenant_id
                || existing.original.allocation_units != request.parent_units
                || self.committed_units(&request.resource_id) > u64::from(request.capacity)
            {
                return Err(ControlError::AssignmentUnavailable(
                    "original allocation tenant or capacity no longer matches".into(),
                ));
            }
            let key = existing.original.allocation_id.clone();
            let existing = self
                .allocations
                .get_mut(&key)
                .expect("found original allocation");
            let borrower = CapacityBorrower {
                task_ref: request.task_ref.clone(),
                role_id: request.role_id.clone(),
                units: request.units,
            };
            if let Some(original) = existing.borrowers.iter().find(|value| {
                value.task_ref == request.task_ref && value.role_id == request.role_id
            }) {
                if original != &borrower {
                    return Err(ControlError::InvalidProposal(
                        "original resource borrower changed its demand".into(),
                    ));
                }
            } else {
                let used: u64 = existing
                    .borrowers
                    .iter()
                    .map(|borrower| u64::from(borrower.units))
                    .sum();
                if used + u64::from(request.units) > u64::from(existing.original.allocation_units) {
                    return Err(ControlError::AssignmentUnavailable(
                        "original Context allocation has no available borrower units".into(),
                    ));
                }
                existing.borrowers.push(borrower);
                existing.borrowers.sort_by(|left, right| {
                    (&left.task_ref, &left.role_id).cmp(&(&right.task_ref, &right.role_id))
                });
            }
            let mut grant = existing.original.clone();
            grant.task_ref = request.task_ref.clone();
            grant.role_id = request.role_id.clone();
            grant.units = request.units;
            return Ok(grant);
        }
        if self.allocations.len() >= 16384
            || self.allocations.values().any(|record| {
                record.original.resource_id == request.resource_id
                    && record.original.node_id != request.node_id
            })
            || self.committed_units(&request.resource_id) + u64::from(request.parent_units)
                > u64::from(request.capacity)
        {
            return Err(ControlError::AssignmentUnavailable(
                "declared pool has no available original capacity".into(),
            ));
        }
        let generation = self
            .generation
            .checked_add(1)
            .ok_or_else(|| ControlError::InvalidProposal("capacity generation exhausted".into()))?;
        let mut grant = ResourceAllocationGrant {
            schema: "roboguide.resource-allocation-grant/v0.1".into(),
            allocation_id: String::new(),
            generation,
            resource_id: request.resource_id.clone(),
            resource_class: request.resource_class.clone(),
            node_id: request.node_id.clone(),
            group_id: request.group_id.clone(),
            owner: request.owner.clone(),
            tenant_id: request.tenant_id.clone(),
            task_ref: request.task_ref.clone(),
            role_id: request.role_id.clone(),
            units: request.units,
            allocation_units: request.parent_units,
            admitted_capacity: request.capacity,
        };
        grant.allocation_id = allocation_id(&grant);
        grant.validate().map_err(ControlError::InvalidProposal)?;
        let scope = if matches!(request.owner, AllocationOwner::Context { .. }) {
            ResourceBindingScope::Context
        } else {
            ResourceBindingScope::Task
        };
        self.allocations.insert(
            grant.allocation_id.clone(),
            CapacityAllocation {
                original: grant.clone(),
                reservation: Reservation {
                    topology: None,
                    task_ref: request.task_ref.clone(),
                    role_id: request.role_id.clone(),
                    group_id: None,
                    scope,
                    owner: request.owner.clone(),
                },
                borrowers: vec![CapacityBorrower {
                    task_ref: request.task_ref.clone(),
                    role_id: request.role_id.clone(),
                    units: request.units,
                }],
            },
        );
        self.generation = generation;
        Ok(grant)
    }

    /// Finds only the original current Task/Role borrower, preserving Context origin history.
    pub(crate) fn binding(
        &self,
        resource: &ResourceId,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
    ) -> Option<&CapacityAllocation> {
        self.allocations.values().find(|record| {
            &record.original.resource_id == resource
                && &record.original.group_id == group
                && record
                    .borrowers
                    .iter()
                    .any(|borrower| &borrower.task_ref == task && &borrower.role_id == role)
        })
    }

    /// Rechecks all live original identities and borrowers without inventing removed historical capacity.
    pub(crate) fn validate(&self) -> Result<(), ControlError> {
        if self.allocations.len() > 16384 {
            return Err(ControlError::InvalidProposal(
                "capacity ledger exceeds its bound".into(),
            ));
        }
        let mut owners = BTreeSet::new();
        let mut generations = BTreeSet::new();
        let mut pools = BTreeMap::<ResourceId, (NodeId, String, u64, u32)>::new();
        for (id, record) in &self.allocations {
            let grant = &record.original;
            let pool = pools.entry(grant.resource_id.clone()).or_insert((
                grant.node_id.clone(),
                grant.resource_class.clone(),
                0,
                grant.admitted_capacity,
            ));
            if pool.0 != grant.node_id || pool.1 != grant.resource_class {
                return Err(ControlError::InvalidProposal(
                    "allocated resource has conflicting original Node or unit classes".into(),
                ));
            }
            pool.2 += u64::from(grant.allocation_units);
            pool.3 = pool.3.max(grant.admitted_capacity);

            grant.validate().map_err(ControlError::InvalidProposal)?;
            if id != &grant.allocation_id
                || id != &allocation_id(grant)
                || grant.generation > self.generation
                || !generations.insert(grant.generation)
                || record.reservation.task_ref != grant.task_ref
                || record.reservation.role_id != grant.role_id
                || record.borrowers.len() > 16384
                || record.reservation.scope
                    != if matches!(grant.owner, AllocationOwner::Context { .. }) {
                        ResourceBindingScope::Context
                    } else {
                        ResourceBindingScope::Task
                    }
                || record.reservation.owner != grant.owner
                || record
                    .reservation
                    .group_id
                    .as_ref()
                    .is_some_and(|group| group != &grant.group_id)
                || !owners.insert(
                    serde_json::to_string(&serde_json::json!([
                        grant.group_id,
                        grant.resource_id,
                        grant.owner,
                        matches!(grant.owner, AllocationOwner::Task(_)).then_some(&grant.role_id)
                    ]))
                    .expect("typed owner serializes"),
                )
            {
                return Err(ControlError::InvalidProposal(
                    "capacity ledger original ownership or generation changed".into(),
                ));
            }
            if record.reservation.group_id.is_none()
                && !record.borrowers.iter().any(|borrower| {
                    borrower.task_ref == grant.task_ref
                        && borrower.role_id == grant.role_id
                        && borrower.units == grant.units
                })
            {
                return Err(ControlError::InvalidProposal(
                    "unbound capacity parent lost its original admitting borrower".into(),
                ));
            }
            let mut borrowers = BTreeSet::new();
            let mut used = 0_u64;
            for borrower in &record.borrowers {
                if borrower.units == 0
                    || borrower.task_ref.mission_id() != grant.task_ref.mission_id()
                    || !borrowers.insert((&borrower.task_ref, &borrower.role_id))
                {
                    return Err(ControlError::InvalidProposal(
                        "capacity borrower identity or units are invalid".into(),
                    ));
                }
                used += u64::from(borrower.units);
            }
            if used > u64::from(grant.allocation_units)
                || matches!(grant.owner, AllocationOwner::Task(_))
                    && (record.borrowers.len() != 1
                        || record.borrowers[0].task_ref != grant.task_ref
                        || record.borrowers[0].role_id != grant.role_id
                        || record.borrowers[0].units != grant.units)
            {
                return Err(ControlError::InvalidProposal(
                    "capacity borrowers exceed original allocation".into(),
                ));
            }
        }
        if pools
            .values()
            .any(|(_, _, units, capacity)| *units > u64::from(*capacity))
        {
            return Err(ControlError::InvalidProposal(
                "original pool commitments exceed every retained source capacity".into(),
            ));
        }
        Ok(())
    }
}

impl ControlPlane {
    /// Returns exact execution grants only for already bound original Task/Role borrowers.
    pub fn resource_allocation_grants(
        &self,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
    ) -> Result<Vec<ResourceAllocationGrant>, ControlError> {
        let mut grants = Vec::new();
        for record in self.capacity_ledger.allocations.values() {
            if &record.original.group_id != group {
                continue;
            }
            if let Some(borrower) = record
                .borrowers
                .iter()
                .find(|value| &value.task_ref == task && &value.role_id == role)
            {
                if record.reservation.group_id.as_ref() != Some(group) {
                    return Err(ControlError::InvalidProposal(
                        "unbound allocation cannot grant Execute".into(),
                    ));
                }
                let mut grant = record.original.clone();
                grant.task_ref = task.clone();
                grant.role_id = role.clone();
                grant.units = borrower.units;
                grants.push(grant);
            }
        }
        grants.sort_by(|left, right| left.resource_id.cmp(&right.resource_id));
        Ok(grants)
    }
}

impl ControlPlane {
    /// Looks up exact existing ownership across exclusive and quantitative authority.
    pub(crate) fn binding_reservation(
        &self,
        resource: &ResourceId,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
    ) -> Option<&Reservation> {
        self.reservations.get(resource).or_else(|| {
            self.capacity_ledger
                .binding(resource, group, task, role)
                .map(|record| &record.reservation)
        })
    }

    /// Updates only an exact admitted borrower's binding, never a sibling owner of the same pool.
    pub(crate) fn binding_reservation_mut(
        &mut self,
        resource: &ResourceId,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
    ) -> Option<&mut Reservation> {
        if self.reservations.contains_key(resource) {
            return self.reservations.get_mut(resource);
        }
        self.capacity_ledger
            .allocations
            .values_mut()
            .find(|record| {
                &record.original.resource_id == resource
                    && &record.original.group_id == group
                    && record
                        .borrowers
                        .iter()
                        .any(|value| &value.task_ref == task && &value.role_id == role)
            })
            .map(|record| &mut record.reservation)
    }

    /// Releases only Task-scoped original claims and ends that Task's Context borrowing.
    pub(crate) fn release_capacity_task(
        &mut self,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: Option<&RoleId>,
    ) {
        self.calendar_version = self.calendar_version.saturating_add(1);
        let mut removed = Vec::new();
        self.capacity_ledger.allocations.retain(|id, record| {
            if &record.original.group_id != group {
                return true;
            }
            record.borrowers.retain(|value| {
                &value.task_ref != task || role.is_some_and(|role| &value.role_id != role)
            });
            let keep = matches!(record.original.owner, AllocationOwner::Context { .. })
                || !record.borrowers.is_empty();
            if !keep {
                removed.push(id.clone());
            }
            keep
        });
        for id in removed {
            self.quota_ledger.release_allocation(&id);
        }
    }

    /// Ends only one original Context's claims, leaving independent Groups and Contexts intact.
    pub(crate) fn release_capacity_context(
        &mut self,
        group: &ExecutionGroupId,
        context: &domain::CoordinationContextId,
    ) {
        self.calendar_version = self.calendar_version.saturating_add(1);
        let mut removed = Vec::new();
        self.capacity_ledger.allocations.retain(|id,record|{
            let remove=&record.original.group_id==group&&matches!(&record.original.owner,AllocationOwner::Context{context_id,..} if context_id==context);
            if remove{removed.push(id.clone());}!remove
        });
        for id in removed {
            self.quota_ledger.release_allocation(&id);
        }
    }

    /// Ends one exact Group only after the existing application stop/revoke release gates have passed.
    pub(crate) fn release_capacity_group(&mut self, group: &ExecutionGroupId) {
        self.calendar_version = self.calendar_version.saturating_add(1);
        let mut removed = Vec::new();
        self.capacity_ledger.allocations.retain(|id, record| {
            let keep = &record.original.group_id != group;
            if !keep {
                removed.push(id.clone());
            }
            keep
        });
        for id in removed {
            self.quota_ledger.release_allocation(&id);
        }
    }

    /// Joins complete original quantitative claims with retained quota and Group/borrower ownership.
    pub(crate) fn validate_capacity_checkpoint(&self) -> Result<(), ControlError> {
        self.capacity_ledger.validate()?;
        for (id, record) in &self.capacity_ledger.allocations {
            let original = &record.original;
            if self.reservations.contains_key(&original.resource_id)
                || self.mission_tenant(original.task_ref.mission_id())
                    != Some(original.tenant_id.as_str())
            {
                return Err(ControlError::InvalidProposal(
                    "capacity claim conflicts with exclusive ownership or original tenant".into(),
                ));
            }
            let group = self.group(&original.group_id).ok_or_else(|| {
                ControlError::InvalidProposal("capacity claim Group is absent".into())
            })?;
            if group.lifecycle() == crate::GroupLifecycle::Released
                || group.mission_id() != original.task_ref.mission_id()
            {
                return Err(ControlError::InvalidProposal(
                    "capacity claim Group changed Mission".into(),
                ));
            }
            let claim = self
                .quota_ledger
                .claims()
                .iter()
                .find(|claim| claim.allocation_id.as_deref() == Some(id))
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "capacity allocation lost original quota claim".into(),
                    )
                })?;
            if claim.claim_id != *id
                || claim.tenant_id != original.tenant_id
                || claim.kind != domain::ResourceKind::Compute
                || claim.units != u64::from(original.allocation_units)
                || claim.resource_id.as_ref() != Some(&original.resource_id)
            {
                return Err(ControlError::InvalidProposal(
                    "capacity quota claim changed its original owner or units".into(),
                ));
            }
            if let AllocationOwner::Context {
                context_id,
                context_role_id,
                ..
            } = &original.owner
            {
                if self.context_capacity_units(
                    &original.group_id,
                    &original.owner,
                    Some(&original.resource_class),
                ) != Some(original.allocation_units)
                {
                    return Err(ControlError::InvalidProposal(
                        "capacity parent changed original Context declaration".into(),
                    ));
                }
                if record.reservation.group_id.is_some()
                    && group
                        .context_binding(context_id, context_role_id)
                        .is_none_or(|binding| {
                            binding.assignment().node_id() != &original.node_id
                                || binding.origin_task_ref() != &original.task_ref
                                || binding.assignment().role_id() != &original.role_id
                                || !binding
                                    .assignment()
                                    .resource_ids()
                                    .contains(&original.resource_id)
                        })
                {
                    return Err(ControlError::InvalidProposal(
                        "Context allocation lost its original retained binding or origin".into(),
                    ));
                }
            }
            for borrower in &record.borrowers {
                let required = group
                    .role_requirement(&borrower.task_ref, &borrower.role_id)
                    .ok_or_else(|| {
                        ControlError::InvalidProposal(
                            "capacity borrower original Role is absent".into(),
                        )
                    })?;
                if !required.resource_requirements().iter().any(|demand| {
                    demand.allocation_mode() == domain::ResourceAllocationMode::Divisible
                        && demand.units() == borrower.units
                        && demand.kind() == domain::ResourceKind::Compute
                        && demand.resource_class() == Some(original.resource_class.as_str())
                }) {
                    return Err(ControlError::InvalidProposal(
                        "capacity borrower changed original Role demand".into(),
                    ));
                }
                if self.capacity_owner(&original.group_id, &borrower.task_ref, &borrower.role_id)?
                    != original.owner
                {
                    return Err(ControlError::InvalidProposal(
                        "capacity borrower changed original Task or Context owner".into(),
                    ));
                }
                let bound = group
                    .task_execution(&borrower.task_ref)
                    .is_some_and(|task| {
                        task.assignments().iter().any(|assignment| {
                            assignment.role_id() == &borrower.role_id
                                && assignment.node_id() == &original.node_id
                                && assignment.resource_ids().contains(&original.resource_id)
                        })
                    });
                let pending = self
                    .pending_recovery_commitments
                    .get(&(
                        original.group_id.clone(),
                        borrower.task_ref.clone(),
                        borrower.role_id.clone(),
                    ))
                    .is_some_and(|commit| {
                        commit.replacement_node_id() == &original.node_id
                            && commit
                                .committed_resource_ids()
                                .contains(&original.resource_id)
                    });
                if record.reservation.group_id.is_some() && bound == pending {
                    return Err(ControlError::InvalidProposal(
                        "capacity borrower is orphaned or both bound and recovery pending".into(),
                    ));
                }
            }
        }
        if self.quota_ledger.claims().iter().any(|claim| {
            claim
                .allocation_id
                .as_ref()
                .is_some_and(|id| !self.capacity_ledger.allocations.contains_key(id))
        }) {
            return Err(ControlError::InvalidProposal(
                "capacity quota claim lost original allocation".into(),
            ));
        }
        Ok(())
    }
}

impl ControlPlane {
    /// Resolves only the frozen owning ContextRole/Task from the accepted Group.
    pub(crate) fn capacity_owner(
        &self,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
    ) -> Result<AllocationOwner, ControlError> {
        let execution = self
            .group(group)
            .and_then(|group| group.task_execution(task))
            .ok_or_else(|| {
                ControlError::InvalidProposal(
                    "capacity admission lacks original TaskExecution".into(),
                )
            })?;
        match execution.role_scope(role) {
            ResourceBindingScope::Task => Ok(AllocationOwner::Task(task.clone())),
            ResourceBindingScope::Context => Ok(AllocationOwner::Context {
                mission_id: task.mission_id().clone(),
                context_id: execution.context_id().clone(),
                context_role_id: execution.context_role(role).cloned().ok_or_else(|| {
                    ControlError::InvalidProposal("capacity ContextRole is absent".into())
                })?,
            }),
        }
    }

    /// Builds the complete quantity and quota batch without changing authority, events or sibling owners.
    pub(crate) fn capacity_commit_candidate<S: ports::SharedNodeStateReader>(
        &self,
        state: &S,
        group: &ExecutionGroupId,
        task: &TaskRef,
        assignments: &[domain::RoleAssignment],
        now: domain::TimestampMs,
    ) -> Result<Option<(CapacityLedger, crate::QuotaLedger)>, ControlError> {
        let original_group = self
            .group(group)
            .ok_or_else(|| ControlError::UnknownGroup(group.clone()))?;
        let any_shared = assignments.iter().any(|assignment| {
            original_group
                .role_requirement(task, assignment.role_id())
                .is_some_and(|role| {
                    role.resource_requirements().iter().any(|demand| {
                        demand.allocation_mode() == domain::ResourceAllocationMode::Divisible
                    })
                })
                || state.node(assignment.node_id()).is_some_and(|node| {
                    node.registration().resources().iter().any(|resource| {
                        assignment.resource_ids().contains(resource.id())
                            && resource.allocation_mode()
                                == domain::ResourceAllocationMode::Divisible
                    })
                })
        });
        if !any_shared {
            return Ok(None);
        }
        let tenant = self.mission_tenant(task.mission_id()).ok_or_else(|| {
            ControlError::InvalidProposal(
                "quantitative Commit requires immutable Mission tenant".into(),
            )
        })?;
        if self
            .quota_ledger
            .has_limit(tenant, domain::ResourceKind::Compute)
            && self.quota_unaccounted_resources.iter().any(|resource| {
                self.reservations.get(resource).is_some_and(|reservation| {
                    self.reservation_quota_tenant(reservation)
                        .is_none_or(|owner| owner == tenant)
                })
            })
        {
            return Err(ControlError::InvalidProposal(
                "legacy tenant demand is unavailable under quantitative quota".into(),
            ));
        }
        let mut ledger = self.capacity_ledger.clone();
        let mut quota = self.quota_ledger.clone();
        for assignment in assignments {
            let role = original_group
                .role_requirement(task, assignment.role_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "quantitative Commit original Role is absent".into(),
                    )
                })?;
            let node = state
                .node(assignment.node_id())
                .ok_or_else(|| ControlError::UnknownNode(assignment.node_id().clone()))?;
            if !self.selected_resources_match_registration(
                node.registration(),
                role,
                assignment.resource_ids(),
            ) {
                return Err(ControlError::AssignmentUnavailable(
                    "selected resource allocation contract no longer matches".into(),
                ));
            }
            let requirements = role.resource_requirements();
            for (resource_id, demand) in assignment.resource_ids().iter().zip(requirements.iter()) {
                if demand.allocation_mode() == domain::ResourceAllocationMode::Exclusive {
                    if ledger.committed_units(resource_id) > 0 {
                        return Err(ControlError::AssignmentUnavailable(
                            "exclusive resource has retained quantitative owners".into(),
                        ));
                    }
                    quota
                        .track_resource(crate::QuotaClaim {
                            claim_id: crate::quota::resource_claim_id(resource_id.as_str()),
                            tenant_id: tenant.into(),
                            kind: demand.kind(),
                            units: u64::from(demand.units()),
                            resource_id: Some(resource_id.clone()),
                            allocation_id: None,
                        })
                        .map_err(|error| ControlError::InvalidProposal(error.to_string()))?;
                    continue;
                }
                if demand.kind() != domain::ResourceKind::Compute
                    || !node
                        .registration()
                        .contract_version()
                        .supports_quantitative_resources()
                    || self.reservations.contains_key(resource_id)
                {
                    return Err(ControlError::AssignmentUnavailable(
                        "quantitative resource contract is unavailable or exclusively owned".into(),
                    ));
                }
                let resource = node
                    .registration()
                    .resources()
                    .iter()
                    .find(|resource| resource.id() == resource_id)
                    .expect("selected resource validated");
                let owner = self.capacity_owner(group, task, assignment.role_id())?;
                let parent_units = match &owner {
                    AllocationOwner::Task(_) => demand.units(),
                    AllocationOwner::Context {
                        context_id,
                        context_role_id,
                        ..
                    } => original_group
                        .context_resource_envelopes
                        .iter()
                        .find(|(context, role)| {
                            context == context_id && role.context_role_id() == context_role_id
                        })
                        .and_then(|(_, role)| {
                            role.resource_envelope()
                                .iter()
                                .find(|limit| limit.resource_class() == demand.resource_class())
                        })
                        .ok_or_else(|| {
                            ControlError::InvalidProposal(
                                "original Context capacity envelope is absent".into(),
                            )
                        })?
                        .units(),
                };
                let request = CapacityRequest {
                    resource_id: resource_id.clone(),
                    resource_class: demand
                        .resource_class()
                        .ok_or_else(|| {
                            ControlError::InvalidProposal(
                                "quantitative unit class is absent".into(),
                            )
                        })?
                        .into(),
                    node_id: assignment.node_id().clone(),
                    group_id: group.clone(),
                    task_ref: task.clone(),
                    role_id: assignment.role_id().clone(),
                    tenant_id: tenant.into(),
                    owner,
                    units: demand.units(),
                    parent_units,
                    capacity: resource.capacity(),
                };
                let grant = ledger.admit(&request)?;
                quota
                    .track_resource(crate::QuotaClaim {
                        claim_id: grant.allocation_id.clone(),
                        tenant_id: tenant.into(),
                        kind: domain::ResourceKind::Compute,
                        units: u64::from(grant.allocation_units),
                        resource_id: Some(resource_id.clone()),
                        allocation_id: Some(grant.allocation_id),
                    })
                    .map_err(|error| ControlError::InvalidProposal(error.to_string()))?;
            }
        }
        self.validate_capacity_calendar(&ledger, task, state, now)?;
        Ok(Some((ledger, quota)))
    }

    /// Returns whether the original role demands exact quantitative ownership for one selected ID.
    pub(crate) fn shared_selection(
        &self,
        group: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
        resource: &ResourceId,
    ) -> bool {
        self.capacity_ledger
            .binding(resource, group, task, role)
            .is_some()
    }

    /// Returns the original quantitative owner of a globally named pool, if any.
    pub(crate) fn capacity_conflict(&self, resource: &ResourceId) -> Option<&Reservation> {
        self.capacity_ledger
            .allocations
            .values()
            .find(|record| &record.original.resource_id == resource)
            .map(|record| &record.reservation)
    }
}
