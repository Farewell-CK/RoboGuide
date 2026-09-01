#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Formal RoboGuide Node Protocol v0.2 transport boundary.
//!
//! Integration owns generated tonic gRPC streaming, concurrent Node sessions,
//! lease fencing, NodeId command routes, and wire validation/conversion. Controller
//! application composition that consumes these facts lives in `core/orchestration`;
//! this crate has no dependency on Control, State, Runtime, or Local EAIOS code.

pub mod grpc;
mod grpc_server;

pub use grpc_server::{GrpcIntegrationService, GrpcNodeEvent, GrpcNodeRouter};
