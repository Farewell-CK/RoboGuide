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
    let mut server = IntegrationServer::bind(&address).await?;
    loop {
        let mut session = server.accept().await?;
        while session.next_event().await?.is_some() {}
    }
}
