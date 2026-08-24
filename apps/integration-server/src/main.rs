#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

use integration::IntegrationServer;

/// Binds the configured integration listener and keeps accepting connector sessions.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let address = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:50051".to_string());
    let server = IntegrationServer::bind(&address).await?;
    let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    tokio::spawn(async move { while receiver.recv().await.is_some() {} });
    server.serve(events).await?;
    Ok(())
}
