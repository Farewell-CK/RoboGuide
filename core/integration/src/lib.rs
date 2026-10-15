#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Formal RoboGuide Node Protocol v0.4 transport boundary.
//!
//! Integration owns generated tonic gRPC streaming, concurrent Node sessions,
//! lease fencing, NodeId command routes, and wire validation/conversion. Controller
//! application composition that consumes these facts lives in `core/orchestration`;
//! this crate has no dependency on Control, State, Runtime, or Local EAIOS code.

pub mod grpc;
mod grpc_server;

pub use domain::{
    IdentityError, IdentityPhase, NODE_IDENTITY_REGISTRY_SCHEMA, NodeIdentityRecord,
    NodeIdentityRegistry,
};
pub use grpc_server::{
    EXECUTION_SESSION_METADATA_KEY, EXECUTION_SESSION_METADATA_VALUE, GrpcIntegrationService,
    GrpcLegacyV02Service, GrpcNodeEvent, GrpcNodeEventCompletion, GrpcNodeEventDelivery,
    GrpcNodeRouter, MAX_MEMORY_IMPORT_MANIFEST_BYTES, MEMORY_IMPORT_METADATA_KEY,
    MEMORY_IMPORT_METADATA_VALUE, NodeIdentityPolicy, memory_import_authorization_digest,
    memory_import_content_digest, memory_import_query_digest,
};
pub use ports::{NodeLeasePolicy, NodeProtocolPolicy};

/// Extracts an already TLS-verified leaf's exclusive expiry; this does not verify its signature/chain.
pub fn verified_certificate_expires_at_ms(certificate: &[u8]) -> Result<u64, tonic::Status> {
    grpc_server::certificate::expires_at_ms(certificate)
}
