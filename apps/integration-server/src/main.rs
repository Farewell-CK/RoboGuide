#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

use integration::GrpcIntegrationService;
use integration::grpc::v0_1::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;

/// Binds the configured integration listener and keeps accepting connector sessions.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let address: std::net::SocketAddr = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:50051".to_string())
        .parse()?;
    let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let (service, _router) = GrpcIntegrationService::new(events);
    tokio::spawn(async move { while receiver.recv().await.is_some() {} });
    tonic::transport::Server::builder()
        .add_service(RoboGuideNodeProtocolServer::new(service))
        .serve(address)
        .await?;
    Ok(())
}
