//! Safe fixed-argument reference backend for proving canonical-to-local operation mapping.

use domain::{CapabilityContractRef, ExecutionGroupId, ExecutionIntent, NodeId, RoleId, TaskRef};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Identity context supplied to a Local EAIOS together with one execution intent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalExecutionContext {
    /// Mission-scoped task being executed.
    task_ref: TaskRef,
    /// Existing collaboration group owning this invocation.
    group_id: ExecutionGroupId,
    /// Role being executed by the target node.
    role_id: RoleId,
    /// Local node receiving the invocation.
    node_id: NodeId,
}

impl LocalExecutionContext {
    /// Creates immutable execution context without interpreting the intent.
    pub const fn new(
        task_ref: TaskRef,
        group_id: ExecutionGroupId,
        role_id: RoleId,
        node_id: NodeId,
    ) -> Self {
        Self {
            task_ref,
            group_id,
            role_id,
            node_id,
        }
    }

    /// Returns the mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the owning execution group.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the role being executed.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the target local node.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }
}

/// A safe local command representation with an executable and fixed arguments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalInvocation {
    /// Preconfigured argv; no element comes from a network command string.
    argv: Vec<String>,
}

impl LocalInvocation {
    /// Returns the configured executable and fixed arguments.
    pub fn argv(&self) -> &[String] {
        &self.argv
    }
}

/// Failures while translating canonical intent into a local backend representation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BackendError {
    /// No local mapping exists for the canonical capability contract.
    UnsupportedOperation(CapabilityContractRef),
    /// A configured operation had no executable or contained a blank argv element.
    InvalidConfiguredCommand(CapabilityContractRef),
}

impl Display for BackendError {
    /// Formats stable backend diagnostics without exposing transport details.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnsupportedOperation(operation) => {
                write!(formatter, "no local mapping for operation {operation}")
            }
            Self::InvalidConfiguredCommand(operation) => {
                write!(formatter, "invalid fixed argv for operation {operation}")
            }
        }
    }
}

impl std::error::Error for BackendError {}

/// Adapter-local boundary that maps canonical intent to a Local EAIOS representation.
pub trait LocalEaiosBackend {
    /// Translates one intent without executing shell text supplied by the network.
    fn translate(
        &self,
        context: &LocalExecutionContext,
        intent: &ExecutionIntent,
    ) -> Result<LocalInvocation, BackendError>;
}

/// Reference backend mapping canonical capability contracts to preconfigured fixed argv.
#[derive(Debug, Clone)]
pub struct ConfiguredCommandBackend {
    /// Whitelisted operation mappings owned by local configuration.
    commands: BTreeMap<CapabilityContractRef, Vec<String>>,
}

impl ConfiguredCommandBackend {
    /// Validates and stores a whitelist of fixed local commands.
    pub fn new(
        commands: BTreeMap<CapabilityContractRef, Vec<String>>,
    ) -> Result<Self, BackendError> {
        if let Some((operation, _)) = commands.iter().find(|(_, argv)| {
            argv.is_empty() || argv.iter().any(|argument| argument.trim().is_empty())
        }) {
            return Err(BackendError::InvalidConfiguredCommand(operation.clone()));
        }
        Ok(Self { commands })
    }
}

impl LocalEaiosBackend for ConfiguredCommandBackend {
    /// Looks up fixed argv by canonical capability contract and ignores network parameters in v0.1.
    fn translate(
        &self,
        _context: &LocalExecutionContext,
        intent: &ExecutionIntent,
    ) -> Result<LocalInvocation, BackendError> {
        let argv = self
            .commands
            .get(intent.capability_contract())
            .cloned()
            .ok_or_else(|| {
                BackendError::UnsupportedOperation(intent.capability_contract().clone())
            })?;
        Ok(LocalInvocation { argv })
    }
}
