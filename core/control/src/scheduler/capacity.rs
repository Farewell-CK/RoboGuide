//! Weighted half-open calendar arithmetic shared by proposal search and authority validation.

use super::{RoleSchedulingSelection, SchedulingOccupancy, SchedulingSnapshot};
use domain::{
    Resource, ResourceAllocationMode, ResourceId, ResourceRequirement, RoleId, TimestampMs,
};
use std::collections::{BTreeMap, BTreeSet};

/// Original Context envelope and its separate borrower accounting axis.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ContextCapacity {
    /// Canonical original Group/ContextRole identity, independent of Task and selected Node.
    pub(crate) owner_axis: String,
    /// Positive original ContextRole declaration, never inferred from a borrower.
    pub(crate) units: u32,
    /// Earliest existing parent occupancy; earlier new borrowers must reserve the missing prefix.
    pub(crate) parent_starts_at: Option<TimestampMs>,
}

impl SchedulingOccupancy {
    /// Creates a shared physical or Context borrower interval without inventing another ResourceId.
    pub(crate) fn quantitative(
        resource_id: ResourceId,
        starts_at: TimestampMs,
        ends_at: Option<TimestampMs>,
        units: u32,
        owner_axis: Option<String>,
    ) -> Self {
        Self {
            resource_id,
            starts_at,
            ends_at,
            units: Some(units),
            owner_axis,
        }
    }
}

/// Checks a complete weighted interval by sweeping simultaneous starts and ends atomically.
pub(crate) fn interval_available<'a>(
    occupancies: impl Iterator<Item = &'a SchedulingOccupancy>,
    start: TimestampMs,
    end: Option<TimestampMs>,
    units: u32,
    capacity: u32,
) -> bool {
    if units > capacity || end.is_some_and(|end| end <= start) {
        return false;
    }
    let mut changes = BTreeMap::<TimestampMs, i128>::new();
    changes.insert(start, i128::from(units));
    for occupancy in occupancies {
        if end.is_some_and(|end| occupancy.starts_at >= end)
            || occupancy.ends_at.is_some_and(|end| end <= start)
        {
            continue;
        }
        let Some(occupied) = occupancy.units else {
            return false;
        };
        let first = start.max(occupancy.starts_at);
        *changes.entry(first).or_default() += i128::from(occupied);
        if let Some(last) = occupancy
            .ends_at
            .filter(|last| end.is_none_or(|end| *last < end))
        {
            *changes.entry(last).or_default() -= i128::from(occupied);
        }
    }
    let mut active = 0_i128;
    for change in changes.values() {
        active += change;
        if active < 0 || active > i128::from(capacity) {
            return false;
        }
    }
    true
}

impl SchedulingSnapshot {
    /// Adds complete selected Role occupancy, counting each new Context parent exactly once.
    pub(crate) fn selected_occupancies(
        &self,
        selected: &[RoleSchedulingSelection],
        start: TimestampMs,
        end: Option<TimestampMs>,
    ) -> Vec<SchedulingOccupancy> {
        let mut result = self.occupancies.clone();
        let mut parents = BTreeSet::new();
        for selection in selected {
            for (index, id) in selection.resource_ids().iter().enumerate() {
                let demand = selection.resource_requirements().get(index);
                if demand.is_none_or(|demand| {
                    demand.allocation_mode() == ResourceAllocationMode::Exclusive
                }) {
                    result.push(SchedulingOccupancy::new(id.clone(), start, end));
                } else {
                    let demand = demand.expect("shared selection has an exact demand");
                    if let Some(context) = self.context_capacities.get(selection.role_id()) {
                        result.push(SchedulingOccupancy::quantitative(
                            id.clone(),
                            start,
                            end,
                            demand.units(),
                            Some(context.owner_axis.clone()),
                        ));
                        if context.parent_starts_at.is_none_or(|starts| starts > start)
                            && parents.insert(context.owner_axis.clone())
                        {
                            result.push(SchedulingOccupancy::quantitative(
                                id.clone(),
                                start,
                                context.parent_starts_at,
                                context.units,
                                None,
                            ));
                        }
                    } else {
                        result.push(SchedulingOccupancy::quantitative(
                            id.clone(),
                            start,
                            end,
                            demand.units(),
                            None,
                        ));
                    }
                }
            }
        }
        result
    }

    /// Tests exact units and the independent original Context envelope against all prior joint choices.
    pub(crate) fn capacity_available(
        &self,
        role: &RoleId,
        resource: &Resource,
        demand: &ResourceRequirement,
        start: TimestampMs,
        end: Option<TimestampMs>,
        selected: &[RoleSchedulingSelection],
    ) -> bool {
        let occupancies = self.selected_occupancies(selected, start, end);
        if let Some(registry) = &self.resource_topology {
            if matches!(
                resource.kind(),
                domain::ResourceKind::Space | domain::ResourceKind::Time
            ) && registry.entry(resource.id()).is_none()
            {
                return false;
            }
            if let Some(entry) = registry.entry(resource.id())
                && (!entry.permits_interval(start, end)
                    || occupancies.iter().any(|occupied| {
                        occupied.resource_id() != resource.id()
                            && crate::resource_topology::overlaps(
                                start,
                                end,
                                occupied.starts_at(),
                                occupied.ends_at(),
                            )
                            && registry
                                .entry(occupied.resource_id())
                                .is_some_and(|other| entry.conflicts(other))
                    }))
            {
                return false;
            }
        }
        let physical = || {
            occupancies
                .iter()
                .filter(|item| item.resource_id() == resource.id() && item.owner_axis.is_none())
        };
        if demand.allocation_mode() == ResourceAllocationMode::Exclusive {
            return interval_available(
                physical(),
                start,
                end,
                resource.capacity(),
                resource.capacity(),
            );
        }
        if let Some(context) = self.context_capacities.get(role) {
            let parent_selected = selected.iter().any(|selection| {
                self.context_capacities
                    .get(selection.role_id())
                    .is_some_and(|other| other.owner_axis == context.owner_axis)
            });
            let needs_parent =
                !parent_selected && context.parent_starts_at.is_none_or(|starts| starts > start);
            let physical_ok = if needs_parent {
                interval_available(
                    physical(),
                    start,
                    context.parent_starts_at,
                    context.units,
                    resource.capacity(),
                )
            } else {
                interval_available(physical(), start, None, 0, resource.capacity())
            };
            physical_ok
                && interval_available(
                    occupancies.iter().filter(|item| {
                        item.resource_id() == resource.id()
                            && item.owner_axis.as_ref() == Some(&context.owner_axis)
                    }),
                    start,
                    end,
                    demand.units(),
                    context.units,
                )
        } else {
            interval_available(physical(), start, end, demand.units(), resource.capacity())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Creates a synthetic interval only; no execution or physical availability is asserted.
    fn occupied(start: u64, end: u64, units: u32) -> SchedulingOccupancy {
        SchedulingOccupancy::quantitative(
            ResourceId::new("unit-pool").unwrap(),
            TimestampMs::new(start),
            Some(TimestampMs::new(end)),
            units,
            None,
        )
    }

    /// Disjoint promises must not be naively added; simultaneous half-open endpoints cancel exactly.
    #[test]
    fn capacity_sweep_preserves_nonoverlapping_and_simultaneous_boundaries() {
        let occupancies = [occupied(0, 10, 3), occupied(10, 20, 3)];
        assert!(interval_available(
            occupancies.iter(),
            TimestampMs::new(0),
            Some(TimestampMs::new(20)),
            1,
            4
        ));
        assert!(!interval_available(
            occupancies.iter(),
            TimestampMs::new(0),
            Some(TimestampMs::new(20)),
            2,
            4
        ));
        assert!(interval_available(
            occupancies.iter(),
            TimestampMs::new(20),
            None,
            4,
            4
        ));
    }

    /// A live Unknown interval cannot expire from a planned end or borrowing arithmetic overflow.
    #[test]
    fn capacity_sweep_retains_unknown_and_wide_arithmetic() {
        let unknown = SchedulingOccupancy::quantitative(
            ResourceId::new("unit-pool").unwrap(),
            TimestampMs::new(0),
            None,
            u32::MAX,
            None,
        );
        assert!(!interval_available(
            std::iter::once(&unknown),
            TimestampMs::new(99999),
            None,
            1,
            u32::MAX
        ));
        let exclusive = SchedulingOccupancy::new(
            ResourceId::new("unit-pool").unwrap(),
            TimestampMs::new(0),
            None,
        );
        assert!(!interval_available(
            std::iter::once(&exclusive),
            TimestampMs::new(1),
            None,
            1,
            u32::MAX
        ));
    }

    /// An earlier borrower must reserve the physical prefix before an existing future Context parent.
    #[test]
    fn context_capacity_checks_the_missing_parent_prefix() {
        let resource = Resource::new_divisible(ResourceId::new("unit-pool").unwrap(), 4).unwrap();
        let role = RoleId::new("worker").unwrap();
        let mut snapshot = SchedulingSnapshot::new(
            1,
            vec![
                occupied(0, 10, 2),
                SchedulingOccupancy::quantitative(
                    resource.id().clone(),
                    TimestampMs::new(10),
                    None,
                    3,
                    None,
                ),
            ],
        );
        snapshot.context_capacities.insert(
            role.clone(),
            ContextCapacity {
                owner_axis: "original-context".into(),
                units: 3,
                parent_starts_at: Some(TimestampMs::new(10)),
            },
        );
        let demand = ResourceRequirement::new_divisible(1).unwrap();
        assert!(!snapshot.capacity_available(
            &role,
            &resource,
            &demand,
            TimestampMs::new(0),
            Some(TimestampMs::new(5)),
            &[]
        ));
        assert!(snapshot.capacity_available(
            &role,
            &resource,
            &demand,
            TimestampMs::new(10),
            Some(TimestampMs::new(15)),
            &[]
        ));
    }

    /// Joint Roles count one original Context parent while independently bounding concurrent borrowers.
    #[test]
    fn context_joint_roles_keep_one_parent_and_the_exact_borrower_sum() {
        let resource = Resource::new_divisible(ResourceId::new("unit-pool").unwrap(), 4).unwrap();
        let roles = [
            RoleId::new("first").unwrap(),
            RoleId::new("second").unwrap(),
        ];
        let mut snapshot = SchedulingSnapshot::new(1, Vec::new());
        for role in &roles {
            snapshot.context_capacities.insert(
                role.clone(),
                ContextCapacity {
                    owner_axis: "original-context".into(),
                    units: 3,
                    parent_starts_at: None,
                },
            );
        }
        let selected = [RoleSchedulingSelection {
            role_id: roles[0].clone(),
            node_id: domain::NodeId::new("unit-node").unwrap(),
            resource_ids: vec![resource.id().clone()],
            resource_units: vec![2],
            resource_requirements: vec![ResourceRequirement::new_divisible(2).unwrap()],
            resource_capacities: vec![4],
        }];
        let start = TimestampMs::new(0);
        let end = Some(TimestampMs::new(10));
        assert!(snapshot.capacity_available(
            &roles[1],
            &resource,
            &ResourceRequirement::new_divisible(1).unwrap(),
            start,
            end,
            &selected
        ));
        assert!(!snapshot.capacity_available(
            &roles[1],
            &resource,
            &ResourceRequirement::new_divisible(2).unwrap(),
            start,
            end,
            &selected
        ));
        let projected = snapshot.selected_occupancies(&selected, start, end);
        assert_eq!(
            projected
                .iter()
                .filter(|row| row.owner_axis.is_none())
                .count(),
            1
        );
        assert_eq!(
            projected
                .iter()
                .find(|row| row.owner_axis.is_none())
                .unwrap()
                .units,
            Some(3)
        );
    }
}
