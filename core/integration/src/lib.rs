#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Formal RoboGuide Node Protocol v0.2 transport and Runtime composition bridge.
//!
//! Integration owns generated tonic gRPC streaming, concurrent Node sessions,
//! lease fencing, NodeId command routes, and conversion into existing Control,
//! Runtime, and Shared State semantics. It contains no Local EAIOS implementation.

pub mod grpc;
mod grpc_server;
mod runtime_bridge;

pub use grpc_server::{GrpcIntegrationService, GrpcNodeEvent, GrpcNodeRouter};
pub use runtime_bridge::{
    CONTROLLER_CHECKPOINT_SCHEMA, IntegrationRuntimeBridge, IntegrationRuntimeError,
    ObservedTaskOutcome, ObservedTaskResult, RemoteExecutionStatus,
};
