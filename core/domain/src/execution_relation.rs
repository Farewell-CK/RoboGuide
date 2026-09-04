//! Mission-owned execution coordination relation specifications and observable states.

use crate::{ContextRoleId, DomainError, ExecutionRelationId, MapRevisionSelector, RoleId, TaskId};

/// One logical Task/Role execution slot independent of Node placement and physical attempts.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct PlannedExecutionRef {
    /// Task containing the logical role execution.
    task_id: TaskId,
    /// Role whose current attempt occupies the logical slot.
    role_id: RoleId,
}

impl PlannedExecutionRef {
    /// Creates one logical endpoint without selecting a Node or execution attempt.
    pub const fn new(task_id: TaskId, role_id: RoleId) -> Self {
        Self { task_id, role_id }
    }

    /// Returns the Task containing this logical execution slot.
    pub const fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns the Role occupying this logical execution slot.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }
}

/// Execution coupling mode selecting the coordination mechanisms required by one Context.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize, Default)]
pub enum ExecutionCouplingMode {
    /// Roles execute independently without a cross-Role coordination mechanism.
    #[default]
    Independent,
    /// Roles require a staged handoff mechanism while preserving DAG ownership of readiness.
    SequentialHandoff,
    /// Concurrent roles exchange selected group evidence and relation observations.
    ConcurrentCooperation,
    /// Concurrent roles additionally require a direct peer coordination channel descriptor.
    TightlyCoupledCooperation,
}

impl ExecutionCouplingMode {
    /// Returns whether this mode requires one coordination mechanism declaration.
    pub const fn requires(self, mechanism: CoordinationMechanism) -> bool {
        match self {
            Self::Independent => false,
            Self::SequentialHandoff => matches!(mechanism, CoordinationMechanism::TaskHandoff),
            Self::ConcurrentCooperation => matches!(
                mechanism,
                CoordinationMechanism::GroupSharedState | CoordinationMechanism::RelationEvidence
            ),
            Self::TightlyCoupledCooperation => matches!(
                mechanism,
                CoordinationMechanism::GroupSharedState
                    | CoordinationMechanism::RelationEvidence
                    | CoordinationMechanism::DirectPeerChannel
            ),
        }
    }
}

/// Coordination mechanism declared by an execution coupling mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum CoordinationMechanism {
    /// Explicit handoff evidence between staged executions.
    TaskHandoff,
    /// Selective group-scoped state view.
    GroupSharedState,
    /// Shared map/frame reference for spatial observations.
    SharedSpatialReference,
    /// Runtime relation evidence and lifecycle fencing.
    RelationEvidence,
    /// Transport-neutral direct peer channel lifecycle.
    DirectPeerChannel,
}

/// One field a Group member may expose through a shared view.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize,
)]
pub enum GroupViewField {
    /// Member pose in the declared shared frame.
    Pose,
    /// Member velocity in the declared shared frame.
    Velocity,
    /// Member execution lifecycle state.
    Execution,
}

/// Shared map/frame reference required to interpret group spatial evidence.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SharedSpatialReference {
    /// Typed immutable map revision identity shared with Spatial Memory.
    selector: MapRevisionSelector,
    /// Common coordinate frame identity.
    frame_id: String,
}

impl SharedSpatialReference {
    /// Creates a shared map/frame reference through the existing typed Spatial identity.
    pub fn new(
        selector: MapRevisionSelector,
        frame_id: impl Into<String>,
    ) -> Result<Self, DomainError> {
        let frame_id = frame_id.into();
        if frame_id.trim().is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "shared spatial frame id must not be blank".to_string(),
            });
        }
        Ok(Self { selector, frame_id })
    }

    /// Returns the immutable Spatial Memory selector.
    pub const fn selector(&self) -> &MapRevisionSelector {
        &self.selector
    }

    /// Returns the common coordinate frame identity.
    pub fn frame_id(&self) -> &str {
        &self.frame_id
    }

    /// Validates a deserialized reference without changing its typed Spatial identity.
    pub fn validate(&self) -> Result<(), DomainError> {
        if self.frame_id.trim().is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "shared spatial frame id must not be blank".to_string(),
            });
        }
        Ok(())
    }
}

/// Explicit State schema binding for one Group member view field.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct GroupViewBinding {
    /// Logical Context member whose node-owned State export supplies the field.
    context_role_id: ContextRoleId,
    /// Typed semantic field exposed to the Group view.
    field: GroupViewField,
    /// Exact node-wide State export identity for State-backed fields.
    #[serde(default)]
    state_export_id: Option<String>,
    /// Exact State payload schema for State-backed fields.
    #[serde(default)]
    payload_schema: Option<String>,
}

impl GroupViewBinding {
    /// Creates a binding without inferring semantics from a channel name.
    pub fn new(
        context_role_id: ContextRoleId,
        field: GroupViewField,
        state_export_id: impl Into<String>,
        payload_schema: impl Into<String>,
    ) -> Result<Self, DomainError> {
        let state_export_id = state_export_id.into();
        let payload_schema = payload_schema.into();
        if field == GroupViewField::Execution {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Execution Group view fields require the Runtime-backed constructor"
                    .to_string(),
            });
        }
        if state_export_id.trim().is_empty() || payload_schema.trim().is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Group view State export identity and payload schema must not be blank"
                    .to_string(),
            });
        }
        Ok(Self {
            context_role_id,
            field,
            state_export_id: Some(state_export_id),
            payload_schema: Some(payload_schema),
        })
    }

    /// Creates an Execution binding backed by Runtime status rather than a Node State export.
    pub const fn new_execution(context_role_id: ContextRoleId) -> Self {
        Self {
            context_role_id,
            field: GroupViewField::Execution,
            state_export_id: None,
            payload_schema: None,
        }
    }

    /// Returns the logical Context member selected by this binding.
    pub const fn context_role_id(&self) -> &ContextRoleId {
        &self.context_role_id
    }

    /// Returns the typed view field.
    pub const fn field(&self) -> GroupViewField {
        self.field
    }

    /// Returns the exact node-wide State export identity.
    pub fn state_export_id(&self) -> Option<&str> {
        self.state_export_id.as_deref()
    }

    /// Returns the exact State payload schema selected by this binding.
    pub fn payload_schema(&self) -> Option<&str> {
        self.payload_schema.as_deref()
    }
}

/// Selective group-scoped state/spatial view declaration.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct GroupSharedViewSpec {
    /// Optional common map/frame interpretation for member spatial values.
    spatial_reference: Option<SharedSpatialReference>,
    /// Explicit field-to-State-schema bindings.
    bindings: Vec<GroupViewBinding>,
    /// Whether consumers receive Fresh/Stale/Unknown metadata for every binding.
    include_freshness: bool,
}

impl GroupSharedViewSpec {
    /// Creates a selective Group view without interpreting any State payload.
    pub fn new(
        spatial_reference: Option<SharedSpatialReference>,
        bindings: Vec<GroupViewBinding>,
        include_freshness: bool,
    ) -> Result<Self, DomainError> {
        let view = Self {
            spatial_reference,
            bindings,
            include_freshness,
        };
        view.validate()?;
        Ok(view)
    }

    /// Validates bindings restored through serde without interpreting their payloads.
    pub fn validate(&self) -> Result<(), DomainError> {
        if self.bindings.is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Group shared view requires at least one binding".to_string(),
            });
        }
        for binding in &self.bindings {
            binding.validate()?;
        }
        if let Some(reference) = &self.spatial_reference {
            reference.validate()?;
        }
        let identities = self
            .bindings
            .iter()
            .map(|binding| {
                (
                    binding.context_role_id(),
                    binding.field(),
                    binding.state_export_id(),
                    binding.payload_schema(),
                )
            })
            .collect::<std::collections::BTreeSet<_>>();
        if identities.len() != self.bindings.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Group shared view contains duplicate bindings".to_string(),
            });
        }
        Ok(())
    }

    /// Returns the optional shared Spatial Memory reference.
    pub const fn spatial_reference(&self) -> Option<&SharedSpatialReference> {
        self.spatial_reference.as_ref()
    }

    /// Returns explicit typed State bindings.
    pub fn bindings(&self) -> &[GroupViewBinding] {
        &self.bindings
    }

    /// Returns whether freshness metadata is requested for each binding.
    pub const fn include_freshness(&self) -> bool {
        self.include_freshness
    }
}

impl GroupViewBinding {
    /// Validates whether this binding uses the authority appropriate to its field.
    fn validate(&self) -> Result<(), DomainError> {
        match self.field {
            GroupViewField::Pose | GroupViewField::Velocity => {
                if self
                    .state_export_id
                    .as_deref()
                    .is_none_or(|value| value.trim().is_empty())
                    || self
                        .payload_schema
                        .as_deref()
                        .is_none_or(|value| value.trim().is_empty())
                {
                    return Err(DomainError::InvalidMissionPlan {
                        reason:
                            "spatial Group view fields require a State export and payload schema"
                                .to_string(),
                    });
                }
            }
            GroupViewField::Execution => {
                if self.state_export_id.is_some() || self.payload_schema.is_some() {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: "Execution Group view fields are Runtime-backed and cannot select State exports"
                            .to_string(),
                    });
                }
            }
        }
        Ok(())
    }
}

/// Transport-neutral direct peer channel declaration for one coordination Context.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PeerChannelSpec {
    /// Stable channel profile identity resolved by deployment configuration.
    pub profile_id: String,
    /// Versioned message schema understood by the Local EAIOS peers.
    pub message_schema: String,
}

/// Typed state requirement token without an embedded control predicate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum RelationStateRequirement {
    /// The selected state must be available to the consumer.
    Available,
    /// The selected state must not be available to the consumer.
    Unavailable,
}

/// Provider-defined freshness policy reference.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct FreshnessPolicyRef {
    /// Stable policy identity interpreted by the responsible provider.
    pub policy_id: String,
}

/// Typed relation descriptor carried alongside logical relation endpoints.
///
/// These descriptors identify future evidence families. They intentionally do not contain
/// distance/angle thresholds, motion formulas, or a free-form predicate language.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionRelationType {
    /// Source remains active while the target is active.
    RequiresActive,
    /// A named state exposed by one logical group member.
    GroupMemberState {
        /// Provider-defined state key.
        state_key: String,
    },
    /// Both members use one immutable map revision and coordinate frame.
    SharedSpatialReference {
        /// Shared spatial reference.
        reference: SharedSpatialReference,
    },
    /// Relative pose evidence in a shared frame, evaluated by Local EAIOS.
    RelativePose {
        /// Coordinate frame for the relative observation.
        frame_id: String,
    },
    /// Relative distance evidence in a shared frame, evaluated by Local EAIOS.
    RelativeDistance {
        /// Coordinate frame for the relative observation.
        frame_id: String,
    },
    /// A named state requirement without a hard-coded predicate formula.
    StateRequirement {
        /// Provider-defined state key.
        state_key: String,
        /// Typed availability requirement.
        requirement: RelationStateRequirement,
    },
    /// A named freshness policy applied to one state key.
    FreshnessRequirement {
        /// Provider-defined state key.
        state_key: String,
        /// Provider-defined freshness policy.
        policy: FreshnessPolicyRef,
    },
}

impl Default for ExecutionRelationType {
    /// Uses the v0.1 lifecycle relation for old serialized specifications.
    fn default() -> Self {
        Self::RequiresActive
    }
}

impl ExecutionRelationType {
    /// Maps the typed descriptor to its compatibility relation family.
    pub const fn kind(&self) -> ExecutionRelationKind {
        match self {
            Self::RequiresActive => ExecutionRelationKind::RequiresActive,
            Self::GroupMemberState { .. } => ExecutionRelationKind::GroupMemberState,
            Self::SharedSpatialReference { .. } => ExecutionRelationKind::SharedSpatialReference,
            Self::RelativePose { .. } => ExecutionRelationKind::RelativePose,
            Self::RelativeDistance { .. } => ExecutionRelationKind::RelativeDistance,
            Self::StateRequirement { .. } => ExecutionRelationKind::StateRequirement,
            Self::FreshnessRequirement { .. } => ExecutionRelationKind::FreshnessRequirement,
        }
    }
}

/// Versioned relation family understood by Runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionRelationKind {
    /// The source must remain Accepted/Running whenever the target is Accepted/Running.
    RequiresActive,
    /// A named member state supplies typed evidence.
    GroupMemberState,
    /// Members share a declared map revision and coordinate frame.
    SharedSpatialReference,
    /// Relative pose evidence is delegated to Local EAIOS.
    RelativePose,
    /// Relative distance evidence is delegated to Local EAIOS.
    RelativeDistance,
    /// A typed state requirement is declared without a control formula.
    StateRequirement,
    /// A typed freshness requirement is declared without a fixed threshold.
    FreshnessRequirement,
}

/// Mission-owned specification for one directional execution-time constraint.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionRelationSpec {
    /// Stable relation identity within one Mission.
    relation_id: ExecutionRelationId,
    /// Logical execution whose active state supplies the condition.
    source: PlannedExecutionRef,
    /// Logical execution constrained by the source condition.
    target: PlannedExecutionRef,
    /// Compatibility relation family.
    kind: ExecutionRelationKind,
    /// Typed relation descriptor for v0.4 and later contracts.
    #[serde(default)]
    relation_type: ExecutionRelationType,
}

impl ExecutionRelationSpec {
    /// Creates a directional relation and rejects a self-referential endpoint pair.
    pub fn new(
        relation_id: ExecutionRelationId,
        source: PlannedExecutionRef,
        target: PlannedExecutionRef,
        kind: ExecutionRelationKind,
    ) -> Result<Self, DomainError> {
        if source == target {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("execution relation {relation_id} references itself"),
            });
        }
        if kind != ExecutionRelationKind::RequiresActive {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "execution relation {relation_id} requires the typed relation constructor"
                ),
            });
        }
        Ok(Self {
            relation_id,
            source,
            target,
            kind,
            relation_type: ExecutionRelationType::RequiresActive,
        })
    }

    /// Creates a typed relation while retaining the compatibility family for older readers.
    pub fn new_typed(
        relation_id: ExecutionRelationId,
        source: PlannedExecutionRef,
        target: PlannedExecutionRef,
        relation_type: ExecutionRelationType,
    ) -> Result<Self, DomainError> {
        if source == target {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("execution relation {relation_id} references itself"),
            });
        }
        validate_typed_relation(&relation_type, &relation_id)?;
        let kind = relation_type.kind();
        Ok(Self {
            relation_id,
            source,
            target,
            kind,
            relation_type,
        })
    }

    /// Returns the stable Mission-scoped relation identity.
    pub const fn relation_id(&self) -> &ExecutionRelationId {
        &self.relation_id
    }

    /// Returns the logical condition-provider endpoint.
    pub const fn source(&self) -> &PlannedExecutionRef {
        &self.source
    }

    /// Returns the logical constrained endpoint.
    pub const fn target(&self) -> &PlannedExecutionRef {
        &self.target
    }

    /// Returns the closed relation behavior.
    pub const fn kind(&self) -> ExecutionRelationKind {
        self.kind
    }

    /// Returns the typed relation descriptor.
    pub const fn relation_type(&self) -> &ExecutionRelationType {
        &self.relation_type
    }
}

/// Validates reserved typed relation fields without interpreting provider policy.
fn validate_typed_relation(
    relation_type: &ExecutionRelationType,
    relation_id: &ExecutionRelationId,
) -> Result<(), DomainError> {
    let nonblank = |value: &str, field: &str| {
        if value.trim().is_empty() {
            Err(DomainError::InvalidMissionPlan {
                reason: format!("execution relation {relation_id} has blank {field}"),
            })
        } else {
            Ok(())
        }
    };
    match relation_type {
        ExecutionRelationType::RequiresActive => Ok(()),
        ExecutionRelationType::GroupMemberState { state_key }
        | ExecutionRelationType::StateRequirement { state_key, .. }
        | ExecutionRelationType::FreshnessRequirement { state_key, .. } => {
            nonblank(state_key, "state_key")
        }
        ExecutionRelationType::SharedSpatialReference { reference } => {
            nonblank(reference.frame_id(), "frame_id")
        }
        ExecutionRelationType::RelativePose { frame_id }
        | ExecutionRelationType::RelativeDistance { frame_id } => nonblank(frame_id, "frame_id"),
    }
}

/// Runtime-derived state of one accepted execution relation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionRelationState {
    /// The target is not currently active, so no live constraint window exists.
    Dormant,
    /// The target is active while the source is dispatched but not yet proven active.
    Pending,
    /// Both current endpoints are Accepted or Running.
    Satisfied,
    /// The target is active while the source is terminal.
    Violated,
    /// The target is active while the source physical state is ambiguous.
    Unknown,
}

impl ExecutionRelationState {
    /// Returns whether this state requires explicit reconciliation before target success.
    pub const fn requires_reconciliation(self) -> bool {
        matches!(self, Self::Violated | Self::Unknown)
    }
}
