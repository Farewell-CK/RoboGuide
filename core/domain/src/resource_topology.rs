//! Explicit deployment geometry and calendars, independent of observed physical occupancy.

use crate::{NodeId, ResourceId, ResourceKind, TimestampMs};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// One original half-open interval in the owning Controller's receive-time clock.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceTimeWindow {
    /// Inclusive local Controller start.
    pub starts_at: TimestampMs,
    /// Exclusive local Controller end; expiry never proves release.
    pub ends_at: TimestampMs,
}

/// Conservative spatial exclusion in one explicitly comparable deployment reference.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceSpaceFootprint {
    /// Explicit shared physical comparison namespace, unrelated to Node identifiers.
    pub conflict_domain: String,
    /// Exact declared coordinate frame; no implicit transform is permitted.
    pub frame: String,
    /// Immutable reference revision; equal frame labels alone do not prove comparability.
    pub anchor_revision: String,
    /// Inclusive lower bound in integer millimetres.
    pub lower_mm: [i64; 3],
    /// Inclusive upper bound in integer millimetres.
    pub upper_mm: [i64; 3],
    /// Conservative inflation on each side, checked without saturating arithmetic.
    pub clearance_mm: u32,
}

impl ResourceSpaceFootprint {
    /// Validates declared references, positive volumes and representable clearance inflation.
    pub fn validate(&self) -> Result<(), String> {
        if [&self.conflict_domain, &self.frame, &self.anchor_revision]
            .into_iter()
            .any(|value| value.trim().is_empty() || value.len() > 256)
            || (0..3).any(|axis| {
                self.lower_mm[axis] >= self.upper_mm[axis]
                    || self.lower_mm[axis]
                        .checked_sub(i64::from(self.clearance_mm))
                        .is_none()
                    || self.upper_mm[axis]
                        .checked_add(i64::from(self.clearance_mm))
                        .is_none()
            })
        {
            return Err("resource footprint reference, bounds or clearance is invalid".into());
        }
        Ok(())
    }

    /// Treats unknown same-domain transforms as conflicts; touching inflated boundaries conflict.
    pub fn conflicts(&self, other: &Self) -> bool {
        if self.conflict_domain != other.conflict_domain {
            return false;
        }
        if self.validate().is_err()
            || other.validate().is_err()
            || self.frame != other.frame
            || self.anchor_revision != other.anchor_revision
        {
            return true;
        }
        (0..3).all(|axis| {
            self.lower_mm[axis] - i64::from(self.clearance_mm)
                <= other.upper_mm[axis] + i64::from(other.clearance_mm)
                && other.lower_mm[axis] - i64::from(other.clearance_mm)
                    <= self.upper_mm[axis] + i64::from(self.clearance_mm)
        })
    }
}

/// Additional authoritative constraints on one actual deployment-declared resource.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceTopologyEntry {
    /// Original advertised Resource identity; this declaration never creates it.
    pub resource_id: ResourceId,
    /// Exact deployment-declared Node owner; accepting metadata does not register it.
    pub node_id: NodeId,
    /// Space or Time, retaining the original positive minimum-unit interpretation.
    pub kind: ResourceKind,
    /// Required only for Space entries.
    pub space: Option<ResourceSpaceFootprint>,
    /// Explicit shared calendar exclusion namespace for Time aliases, required only for Time.
    pub time_domain: Option<String>,
    /// Canonical openings in the local Controller clock; empty means unrestricted Space only.
    pub windows: Vec<ResourceTimeWindow>,
}

impl ResourceTopologyEntry {
    /// Checks a complete bounded calendar without assuming any device or source-clock fact.
    pub fn validate(&self) -> Result<(), String> {
        if self.windows.len() > 128
            || match self.kind {
                ResourceKind::Compute => true,
                ResourceKind::Space => self.space.is_none() || self.time_domain.is_some(),
                ResourceKind::Time => {
                    self.space.is_some()
                        || self.windows.is_empty()
                        || self
                            .time_domain
                            .as_ref()
                            .is_none_or(|domain| domain.trim().is_empty() || domain.len() > 256)
                }
            }
        {
            return Err("resource topology kind, footprint or calendar is invalid".into());
        }
        if let Some(space) = &self.space {
            space.validate()?;
        }
        let mut previous = None;
        for window in &self.windows {
            if window.starts_at >= window.ends_at
                || previous.is_some_and(|end| end >= window.starts_at)
            {
                return Err("resource windows overlap or lack canonical positive bounds".into());
            }
            previous = Some(window.ends_at);
        }
        Ok(())
    }

    /// Requires the entire proposed interval to fit one opening; an unknown end cannot fit a window.
    pub fn permits_interval(&self, start: TimestampMs, end: Option<TimestampMs>) -> bool {
        if self.validate().is_err() || end.is_some_and(|end| end <= start) {
            return false;
        }
        self.windows.is_empty()
            || end.is_some_and(|end| {
                self.windows
                    .iter()
                    .any(|window| window.starts_at <= start && end <= window.ends_at)
            })
    }

    /// Finds logical spatial exclusion across distinct IDs, without treating Time labels as geometry.
    pub fn conflicts(&self, other: &Self) -> bool {
        self.resource_id == other.resource_id
            || self
                .time_domain
                .as_ref()
                .zip(other.time_domain.as_ref())
                .is_some_and(|(left, right)| left == right)
            || self
                .space
                .as_ref()
                .zip(other.space.as_ref())
                .is_some_and(|(left, right)| left.conflicts(right))
    }
}

/// Complete, original-principal revisioned Control deployment constraints.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceTopologyRegistry {
    /// Explicit interpretation; no historical resource label acquires geometry implicitly.
    pub schema: String,
    /// Stable original registry identity.
    pub registry_id: String,
    /// Complete monotonic generation.
    pub revision: u64,
    /// Only local Controller receive time is comparable in this contract.
    pub clock: String,
    /// Original maximum wait for independent stop evidence after an execution overruns its booking.
    pub stop_timeout_ms: u64,
    /// Deployment-declared Task booking budget, independent of model planning estimates.
    pub booking_duration_ms: u64,
    /// Maximum age since original Mission acceptance for a new booking; retries never renew it.
    pub booking_max_age_ms: u64,
    /// Complete entries in ResourceId order.
    pub resources: Vec<ResourceTopologyEntry>,
}

impl ResourceTopologyRegistry {
    /// Validates closed bounds and one exact frame/anchor per physical conflict domain.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.resource-topology/v0.1"
            || self.clock != "controller_receive_time"
            || self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.revision == 0
            || self.stop_timeout_ms == 0
            || self.stop_timeout_ms > 3_600_000
            || self.booking_duration_ms == 0
            || self.booking_duration_ms > 3_600_000
            || self.booking_max_age_ms < self.booking_duration_ms
            || self.booking_max_age_ms > 604_800_000
            || self.resources.len() > 4096
        {
            return Err("resource topology schema, clock, identity or bounds are invalid".into());
        }
        let mut references = BTreeMap::new();
        let mut previous = None;
        for entry in &self.resources {
            entry.validate()?;
            if previous.is_some_and(|old| old >= &entry.resource_id) {
                return Err("resource topology identities are duplicated or not canonical".into());
            }
            previous = Some(&entry.resource_id);
            if let Some(space) = &entry.space {
                let reference = (&space.frame, &space.anchor_revision);
                if references
                    .insert(&space.conflict_domain, reference)
                    .is_some_and(|old| old != reference)
                {
                    return Err(
                        "one spatial conflict domain cannot hide incompatible frames".into(),
                    );
                }
            }
        }
        Ok(())
    }

    /// Returns an exact declared constraint without guessing geometry for absent resources.
    pub fn entry(&self, id: &ResourceId) -> Option<&ResourceTopologyEntry> {
        self.resources.iter().find(|entry| &entry.resource_id == id)
    }
}

/// Original deployment evidence attached to the same exclusive Control reservation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceTopologyReservation {
    /// Original complete policy revision.
    pub revision: u64,
    /// Exact original resource declaration, immutable until normal release.
    pub entry: ResourceTopologyEntry,
    /// Original admission instant in the Controller clock.
    pub admitted_at: TimestampMs,
    /// Original booked end; neither this value nor expiry proves release.
    pub planned_end: Option<TimestampMs>,
}
